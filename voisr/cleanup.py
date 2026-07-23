import re

FILLER_RE = re.compile(
    r"\b(?:um+|uh+|uhm+|erm+|er|ah+|hmm+)\b[,.!?]?\s*",
    re.IGNORECASE,
)

PROTECTED_TOKEN_RE = re.compile(
    r"""
    (?:https?://|www\.)[^\s]+                         # URL
    | [a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}          # email
    | \b(?:\d{1,3}\.){3}\d{1,3}\b                   # IPv4-shaped token
    | \b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[ap]\.?m\.?)?\b  # time
    | \bv?\d+(?:[.,]\d+)+\b                          # decimal/version/grouped number
    | \b(?:[a-z]\.){2,}                               # dotted abbreviation
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _protect_tokens(text: str) -> tuple[str, dict[str, str]]:
    protected: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        placeholder = chr(0xE000 + len(protected))
        protected[placeholder] = match.group(0)
        return placeholder

    return PROTECTED_TOKEN_RE.sub(replace, text), protected


def _restore_tokens(text: str, protected: dict[str, str]) -> str:
    for placeholder, original in protected.items():
        text = text.replace(placeholder, original)
    return text


def clean(text: str, dictionary: dict[str, str] | None = None) -> str:
    """Normalize a raw transcript: strip fillers, fix spacing/punctuation,
    apply the user's spoken->written dictionary, capitalize the first letter."""
    if not text.strip():
        return ""
    out, protected = _protect_tokens(text)
    out = FILLER_RE.sub("", out)
    for spoken, written in (dictionary or {}).items():
        # whole-word match (don't rewrite inside longer words); replacement is
        # a callable so backslashes in `written` stay literal
        pattern = re.escape(spoken)
        if spoken[:1].isalnum():
            pattern = r"\b" + pattern
        if spoken[-1:].isalnum():
            pattern = pattern + r"\b"
        out = re.sub(pattern, lambda m: written, out, flags=re.IGNORECASE)
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r"\s+([,.;:!?])", r"\1", out)
    out = re.sub(r"^[,.;:\s]+", "", out)
    out = re.sub(r"([,.;:!?])(?=\S)", r"\1 ", out)
    if out and out[0].islower():
        out = out[0].upper() + out[1:]
    return _restore_tokens(out, protected)
