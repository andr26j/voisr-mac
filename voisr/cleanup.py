import re

FILLER_RE = re.compile(r"\b(?:um+|uh+|uhm+|erm+|er|ah+|hmm+)\b[,.]?\s*", re.IGNORECASE)


def clean(text: str, dictionary: dict[str, str] | None = None) -> str:
    """Normalize a raw transcript: strip fillers, fix spacing/punctuation,
    apply the user's spoken->written dictionary, capitalize the first letter."""
    if not text.strip():
        return ""
    out = FILLER_RE.sub("", text)
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
    return out
