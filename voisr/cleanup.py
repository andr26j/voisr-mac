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


def _split_url_suffix(token: str) -> tuple[str, str]:
    opening = {")": "(", "]": "[", "}": "{"}
    end = len(token)
    while end:
        char = token[end - 1]
        if char in opening and token[:end].count(char) > token[:end].count(opening[char]):
            end -= 1
        elif char in "'\"" and token[:end].count(char) % 2:
            end -= 1
        else:
            break
    return token[:end], token[end:]


def _protected_spans(text: str):
    for match in PROTECTED_TOKEN_RE.finditer(text):
        token = match.group(0)
        if match.lastgroup == "url":
            token, _ = _split_url_suffix(token)
        yield match.start(), match.start() + len(token), token


def _placeholder(text: str, protected: dict[str, str]) -> str:
    length = 1
    while True:
        placeholder = "\uE000" + "\uE001" * length + "\uE002"
        if placeholder not in text and placeholder not in protected:
            return placeholder
        length += 1


def _protect_tokens(text: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}
    parts: list[str] = []
    last = 0
    for start, end, token in _protected_spans(text):
        parts.append(text[last:start])
        placeholder = _placeholder(text, protected)
        protected[placeholder] = token
        parts.append(placeholder)
        last = end
    parts.append(text[last:])
    return "".join(parts), protected


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


def _transform_unprotected_segments(text: str, dictionary: dict[str, str] | None) -> str:
    parts: list[str] = []
    last = 0
    for start, end, token in _protected_spans(text):
        parts.append(_apply_dictionary(FILLER_RE.sub("", text[last:start]), dictionary))
        parts.append(token)
        last = end
    parts.append(_apply_dictionary(FILLER_RE.sub("", text[last:]), dictionary))
    return "".join(parts)


def _restore_tokens(text: str, protected: dict[str, str]) -> str:
    for placeholder, original in protected.items():
        text = text.replace(placeholder, original)
    return text


def clean(text: str, dictionary: dict[str, str] | None = None) -> str:
    """Normalize a raw transcript: strip fillers, fix spacing/punctuation,
    apply the user's spoken->written dictionary, capitalize the first letter."""
    if not text.strip():
        return ""
    out = _transform_unprotected_segments(text, dictionary)
    out, protected = _protect_tokens(out)
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"^[,.;:\s]+", "", out)
    out = re.sub(r"([,.;:!?])(?=\S)", r"\1 ", out)
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    return _restore_tokens(out, protected)
