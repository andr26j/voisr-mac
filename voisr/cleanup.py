import re

FILLER_RE = re.compile(
    r"\b(?:um+|uh+|uhm+|erm+|er|ah+|hmm+)\b[,.!?]?\s*",
    re.IGNORECASE,
)

PROTECTED_TOKEN_RE = re.compile(
    r"""
    (?P<url>(?<![\w._%+-])(?:https?://|www\.)[^\s]+)  # URL
    | [a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}          # email
    | \b(?:\d{1,3}\.){3}\d{1,3}\b                   # IPv4-shaped token
    | \b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[ap]\.?m\.?)?(?=\s|$|[,.;:!?\)\]\}'"])  # time
    | \bv?\d+(?:[.,]\d+)+\b                          # decimal/version/grouped number
    | \b(?:[a-z]\.){2,}                               # dotted abbreviation
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _split_url_suffix(token: str, opening_quote: str | None = None) -> tuple[str, str]:
    opening = {"(": ")", "[": "]", "{": "}"}
    closing = {value: key for key, value in opening.items()}
    closing_quote_index = token.rfind(opening_quote) if opening_quote else -1
    preserve_unmatched_quote_context = opening_quote is not None and closing_quote_index < 0
    stack: list[str] = []
    for index, char in enumerate(token):
        if char in opening:
            stack.append(char)
        elif char in closing:
            if stack and stack[-1] == closing[char]:
                stack.pop()
            else:
                return token[:index], token[index:]
        elif index == closing_quote_index:
            return token[:index], token[index:]
        elif not preserve_unmatched_quote_context and char in "'\"":
            suffix = token[index + 1 :]
            if token.count(char) % 2 and (not suffix or suffix[0] in ",.;:!?)]}"):
                return token[:index], token[index:]
    return token, ""


def _protected_spans(text: str):
    for match in PROTECTED_TOKEN_RE.finditer(text):
        token = match.group(0)
        if match.lastgroup == "url":
            opening_quote = text[match.start() - 1] if match.start() else None
            token, _ = _split_url_suffix(
                token, opening_quote if opening_quote and opening_quote in "'\"" else None
            )
        yield match.start(), match.start() + len(token), token


def _placeholder(text: str, protected: dict[str, str]) -> str:
    length = 1
    while True:
        placeholder = "\uE000" + "\uE001" * length + "\uE002"
        if placeholder not in text and placeholder not in protected:
            return placeholder
        length += 1


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
) -> tuple[str, dict[str, str]]:
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
    protected: dict[str, str] = {}
    parts: list[str] = []
    for gap, (_, _, token) in zip(gaps, spans):
        parts.append(gap)
        placeholder = _placeholder(reserved, protected)
        protected[placeholder] = token
        parts.append(placeholder)
    parts.append(gaps[-1])
    return "".join(parts), protected


def _restore_tokens(text: str, protected: dict[str, str]) -> str:
    for placeholder, original in protected.items():
        text = text.replace(placeholder, original)
    return text


def clean(text: str, dictionary: dict[str, str] | None = None) -> str:
    """Normalize a raw transcript: strip fillers, fix spacing/punctuation,
    apply the user's spoken->written dictionary, capitalize the first letter."""
    if not text.strip():
        return ""
    out, protected = _protect_original_spans(text, dictionary)
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"^[,.;:\s]+", "", out)
    out = re.sub(r"([,.;:!?])(?=\S)", r"\1 ", out)
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    return _restore_tokens(out, protected)
