import re

FILLER_RE = re.compile(
    r"\b(?:um+|uh+|uhm+|erm+|er|ah+|hmm+)\b[,.!?]?\s*",
    re.IGNORECASE,
)

PROTECTED_TOKEN_RE = re.compile(
    r"""
    (?P<url>(?<![\w._%+-])(?:https?://|www\.)[^\s]+)  # URL
    | [\w.!#$%&'*+/=?^`{|}~-]+@                       # email
      [^\W_](?:[^\W_]|-)*(?:\.[^\W_](?:[^\W_]|-)*)+
    | \b(?:\d{1,3}\.){3}\d{1,3}\b                   # IPv4-shaped token
    | \b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[ap]\.?m\.?)?(?=$|[^\w])  # time
    | (?<![\w.])[-+]?(?:\d+\.\d+|\.\d+)(?![\w.])   # signed decimal
    | \bv?\d+(?:[.,]\d+)+\b                          # decimal/version/grouped number
    | \b(?:[a-z]{1,3}\.){2,}                          # dotted abbreviation
    | \b(?:dr|mr|mrs|ms|prof|sr|jr|st)\.               # title
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
            suffix = token[index + 1 :]
            if straight_quote_counts[char] % 2 and (not suffix or suffix[0] in ",.;:!?)]}>"):
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
    used = set(content)
    for codepoint in range(0xE000, 0xF900):
        candidate = chr(codepoint)
        if candidate not in used:
            return candidate

    namespace = "\uE000"
    while namespace in content:
        namespace += "\uE000"
    return namespace


def _apply_dictionary(text: str, dictionary: dict[str, str] | None) -> str:
    out = text
    for spoken, written in (dictionary or {}).items():
        # whole-word match (don't rewrite inside longer words); replacement is
        # a callable so backslashes in `written` stay literal
        pattern = re.escape(spoken)
        if spoken[:1].isalnum():
            pattern = r"\b" + pattern
        if spoken[-1:].isalnum():
            pattern = pattern + r"\b"
        out = re.sub(pattern, lambda m: written, out, flags=re.IGNORECASE)
    return out


def _protect_original_spans(
    text: str, dictionary: dict[str, str] | None
) -> tuple[str, list[str], re.Pattern[str]]:
    spans = list(_protected_spans(text))
    gaps: list[str] = []
    last = 0
    for start, end, _ in spans:
        gaps.append(_apply_dictionary(FILLER_RE.sub("", text[last:start]), dictionary))
        last = end
    gaps.append(_apply_dictionary(FILLER_RE.sub("", text[last:]), dictionary))

    reserved_parts = [text]
    for gap, (_, _, token) in zip(gaps, spans):
        reserved_parts.extend((gap, token))
    reserved_parts.append(gaps[-1])
    reserved = "".join(reserved_parts)
    namespace = _placeholder_namespace(reserved)
    placeholder_re = re.compile(re.escape(namespace) + r"([0-9a-f]+)" + re.escape(namespace))
    protected: list[str] = []
    parts: list[str] = []
    for gap, (_, _, token) in zip(gaps, spans):
        parts.append(gap)
        parts.append(f"{namespace}{len(protected):x}{namespace}")
        protected.append(token)
    parts.append(gaps[-1])
    return "".join(parts), protected, placeholder_re


def _restore_tokens(text: str, protected: list[str], placeholder_re: re.Pattern[str]) -> str:
    return placeholder_re.sub(lambda match: protected[int(match.group(1), 16)], text)


def clean(text: str, dictionary: dict[str, str] | None = None) -> str:
    """Normalize a raw transcript: strip fillers, fix spacing/punctuation,
    apply the user's spoken->written dictionary, capitalize the first letter."""
    if not text.strip():
        return ""
    out, protected, placeholder_re = _protect_original_spans(text, dictionary)
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"^[,.;:\s]+", "", out)
    out = re.sub(r"([,.;:!?])(?=\S)", r"\1 ", out)
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    return _restore_tokens(out, protected, placeholder_re)
