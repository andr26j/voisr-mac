import hashlib
import re
from collections.abc import Callable

# Case-sensitive on purpose: whisper writes fillers lowercase, or capitalised
# at a sentence start, so "ER" (the ward) is a word. A hyphen next to it means
# a word too ("uh-oh"). Only the filler's own comma goes; a following . ? !
# ends the sentence and stays. A filler alone in parentheses goes with them.
_FILLER = r"(?:[Uu]m+|[Uu]h+|[Uu]hm+|[Ee]rm+|[Ee]r|[Aa]h+|[Hh]mm+)"
FILLER_RE = re.compile(
    rf"\(\s*{_FILLER}\s*\)\s*|(?<![\w-]){_FILLER}(?![\w-]),?\s*"
)

# whisper.cpp annotates non-speech instead of returning nothing: a key click
# or room noise comes back as `[BLANK_AUDIO]`, `[ Silence ]`, `(wind blowing)`.
# Whisper's square-bracket tags are plain words, so a bracketed run of words
# goes (a bracketed URL or number stays). Parentheses can be dictated, so only
# those naming a sound go.
NON_SPEECH_RE = re.compile(
    r"""
    \[[\sA-Za-z_'-]*\]
    | \((?=[^()]*\b(?:blank|silence|silent|music|inaudible|noise|wind|
                      blowing|laugh\w*|cough\w*|sigh\w*|applause|static|
                      beep\w*|breath\w*|click\w*|typing|keyboard|mumbl\w*|
                      sniff\w*)\b)[^()]*\)
    """,
    re.IGNORECASE | re.VERBOSE,
)

PROTECTED_TOKEN_RE = re.compile(
    r"""
    (?P<url>(?<![\w._%+-])(?:https?://|www\.)[^\s]+)  # URL
    | (?<![\w.!#$%&'*+/=?^`{|}~-])                    # email start
      (?>[\w.!#$%&'*+/=?^`{|}~-]+)@
      [^\W_](?:[^\W_]|-)*(?:\.[^\W_](?:[^\W_]|-)*)+
    | \b(?:\d{1,3}\.){3}\d{1,3}\b                   # IPv4-shaped token
    | \b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[ap]\.?m\.?)?(?=$|[^\w])  # time
    | (?<![\w.])[-+]?(?:\d+\.\d+|\.\d+)(?![\w.])   # signed decimal
    | (?<![\w.])[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w.])  # grouped number
    | \bv?\d+(?:[.,]\d+)+\b                          # decimal/version/grouped number
    | \bph\.d\.                                             # degree
    | \b(?:[a-z]\.){2,}                                     # abbreviation
    | \b(?:dr|mr|mrs|ms|prof|sr|jr|st)\.               # title
    | (?-i:(?<![\w.])(?![A-Z]\.)[^\W_]\w*(?:\.[a-z0-9][\w-]*)+)
      # dotted name: google.com, config.json, Node.js. A capital after the
      # dot ("yes.No") or a lone capital before it ("I.am") is prose instead.
    """,
    re.IGNORECASE | re.VERBOSE,
)

WRAPPER_PAIRS = {
    "(": ")",
    "[": "]",
    "{": "}",
    "<": ">",
    '"': '"',
    "'": "'",
    "“": "”",
    "‘": "’",
    "«": "»",
    "‹": "›",
}
BRACKET_OPENERS = {"(", "[", "{", "<"}
BRACKET_CLOSERS = {WRAPPER_PAIRS[opener]: opener for opener in BRACKET_OPENERS}
QUOTE_CLOSERS = set(WRAPPER_PAIRS.values()) - set(BRACKET_CLOSERS)
STRAIGHT_QUOTES = {"'", '"'}


def _split_url_suffix(token: str, expected_closer: str | None = None) -> tuple[str, str]:
    closing_quote_index = token.rfind(expected_closer) if expected_closer in QUOTE_CLOSERS else -1
    preserve_unmatched_quote_context = expected_closer in QUOTE_CLOSERS and closing_quote_index < 0
    straight_quote_counts = {quote: token.count(quote) for quote in STRAIGHT_QUOTES}
    stack: list[str] = []
    for index, char in enumerate(token):
        if char in BRACKET_OPENERS:
            stack.append(char)
        elif char in BRACKET_CLOSERS:
            if stack and stack[-1] == BRACKET_CLOSERS[char]:
                stack.pop()
            else:
                return token[:index], token[index:]
        elif index == closing_quote_index:
            return token[:index], token[index:]
        elif not preserve_unmatched_quote_context and char in "'\"":
            next_char = token[index + 1] if index + 1 < len(token) else None
            if straight_quote_counts[char] % 2 and (
                next_char is None or next_char in ",.;:!?)]}>"
            ):
                return token[:index], token[index:]
    return token, ""


def _protected_spans(text: str):
    cursor = 0
    while match := PROTECTED_TOKEN_RE.search(text, cursor):
        token = match.group(0)
        if match.lastgroup == "url":
            opener = text[match.start() - 1] if match.start() else None
            token, _ = _split_url_suffix(token, WRAPPER_PAIRS.get(opener))
        end = match.start() + len(token)
        if end <= cursor:
            cursor = match.end()
            continue
        yield match.start(), end, token
        cursor = end


def _placeholder_namespace(content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    counter = 0
    while True:
        namespace = f"\uE000{digest}{counter:x}\uE001"
        if namespace not in content:
            return namespace
        counter += 1


def _dictionary_rewriter(dictionary: dict[str, str] | None) -> Callable[[str], str]:
    """One pass, longest phrase first: a replacement never feeds another
    entry ({"york": ..., "new york": ...} gave "New Yorkshire"), and a space
    in a phrase matches any run of whitespace (whisper may break a line).
    Built once per clean(): it runs on every gap between protected tokens."""
    entries = sorted(((k, v) for k, v in (dictionary or {}).items() if k.strip()),
                     key=lambda kv: len(kv[0]), reverse=True)
    if not entries:
        return lambda text: text
    patterns = []
    for i, (spoken, _) in enumerate(entries):
        # whole-word match: don't rewrite inside longer words
        pattern = r"\s+".join(re.escape(part) for part in spoken.split())
        if spoken.strip()[:1].isalnum():
            pattern = r"\b" + pattern
        if spoken.strip()[-1:].isalnum():
            pattern = pattern + r"\b"
        patterns.append(f"(?P<d{i}>{pattern})")
    compiled = re.compile("|".join(patterns), re.IGNORECASE)

    def rewrite(text: str) -> str:
        # a callable replacement keeps backslashes in `written` literal
        return compiled.sub(lambda m: entries[int(m.lastgroup[1:])][1], text)

    return rewrite


def _protect_original_spans(
    text: str, dictionary: dict[str, str] | None
) -> tuple[str, list[str], re.Pattern[str]]:
    spans = list(_protected_spans(text))
    rewrite = _dictionary_rewriter(dictionary)
    gaps: list[str] = []
    last = 0
    for start, end, _ in spans:
        gaps.append(rewrite(FILLER_RE.sub("", text[last:start])))
        last = end
    gaps.append(rewrite(FILLER_RE.sub("", text[last:])))

    reserved_parts = [text]
    for gap, (_, _, token) in zip(gaps, spans):
        reserved_parts.extend((gap, token))
    reserved_parts.append(gaps[-1])
    reserved = "".join(reserved_parts)
    namespace = _placeholder_namespace(reserved)
    placeholder_re = re.compile(re.escape(namespace) + r"([0-9a-f]+)" + re.escape(namespace))
    protected: list[str] = []

    def hold(token: str) -> str:
        protected.append(token)
        return f"{namespace}{len(protected) - 1:x}{namespace}"

    # The user's replacement text is final: "e.g." or "12:00" must not get
    # the spacing pass. Only values it could touch are held, so a plain
    # replacement at the start is still capitalised.
    written = sorted({w for w in (dictionary or {}).values()
                      if re.search(r"[,.;:!?]", w)}, key=len, reverse=True)
    written_re = re.compile("|".join(map(re.escape, written))) if written else None

    def hold_written(gap: str) -> str:
        if written_re is None:
            return gap
        return written_re.sub(lambda m: hold(m.group(0)), gap)

    parts: list[str] = []
    for gap, (_, _, token) in zip(gaps, spans):
        parts.append(hold_written(gap))
        parts.append(hold(token))
    parts.append(hold_written(gaps[-1]))
    return "".join(parts), protected, placeholder_re


def _restore_tokens(text: str, protected: list[str], placeholder_re: re.Pattern[str]) -> str:
    return placeholder_re.sub(lambda match: protected[int(match.group(1), 16)], text)


def clean(text: str, dictionary: dict[str, str] | None = None) -> str:
    """Normalize a raw transcript: strip fillers, fix spacing/punctuation,
    apply the user's spoken->written dictionary, capitalize the first letter."""
    text = NON_SPEECH_RE.sub(" ", text)
    if not text.strip():
        return ""
    out, protected, placeholder_re = _protect_original_spans(text, dictionary)
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    # a dropped filler can leave its comma against the sentence end ("it, uh?")
    out = re.sub(r",(?=[.?!])", "", out)
    out = re.sub(r"^[,.;:!?\s]+", "", out)
    # space after punctuation, but never inside a run ("...", "?!")
    out = re.sub(r"([,.;:!?])(?=[^\s,.;:!?])", r"\1 ", out)
    first_word = out.split(" ", 1)[0]
    if out and out[0].islower() and not any(c.isupper() for c in first_word):
        out = out[0].upper() + out[1:]  # but leave iPhone and eBay alone
    return _restore_tokens(out, protected, placeholder_re)
