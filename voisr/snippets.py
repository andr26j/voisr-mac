"""Saved text that a whole dictation can stand in for: {trigger: text} in
config.json. A trigger keeps the spelling it was saved with; the text is the
user's and is never altered. Nothing here logs either of them."""
import re
from dataclasses import dataclass

from voisr.cleanup import NON_SPEECH_RE

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'"})
_OUTER_PUNCTUATION = re.compile(r"^[\W_]+|[\W_]+$")
SHORT_TRIGGER_LETTERS = 6  # fewer, or a single word, is easy to say by accident
# What Whisper tends to make of a stray noise on its own.
_NOISE_PHRASES = {"thank you", "thanks", "thank you very much", "thanks for watching",
                  "you", "okay", "ok", "bye", "bye bye", "yeah", "so", "the end"}


class SnippetError(ValueError):
    """Why a snippet can't be saved, in words for the page."""


@dataclass(frozen=True)
class Snippet:
    trigger: str
    text: str


def normalize_trigger(text: str) -> str:
    """What a trigger and a transcript are compared as: no capitals, no
    punctuation round the outside, single spaces. Inside is left alone, so
    "e-mail" is not "email"."""
    words = " ".join(text.translate(_APOSTROPHES).split()).casefold()
    return _OUTER_PUNCTUATION.sub("", words)


def find_snippet(raw: str, snippets) -> Snippet | None:
    """The snippet this whole transcript names, if any. Takes Whisper's text
    before clean() or the Dictionary have changed it; its non-speech tags
    ("[BLANK_AUDIO]") aren't words anyone said, so they don't count."""
    if not snippets:
        return None
    spoken = normalize_trigger(NON_SPEECH_RE.sub(" ", raw))
    if not spoken:
        return None
    for trigger, text in snippets.items():
        if normalize_trigger(trigger) == spoken:
            return Snippet(trigger, text)
    return None


def _entry_ok(trigger, text) -> bool:
    return (isinstance(trigger, str) and isinstance(text, str)
            and bool(normalize_trigger(trigger)) and bool(text.strip()))


def is_valid(snippets) -> bool:
    if not isinstance(snippets, dict):
        return False
    if not all(_entry_ok(trigger, text) for trigger, text in snippets.items()):
        return False
    return len({normalize_trigger(trigger) for trigger in snippets}) == len(snippets)


def salvage(snippets: dict) -> dict:
    """The good entries of a hand-edited value. Of triggers that match the same
    speech the first stays, as that is the one find_snippet would use."""
    good, seen = {}, set()
    for trigger, text in snippets.items():
        if _entry_ok(trigger, text) and normalize_trigger(trigger) not in seen:
            seen.add(normalize_trigger(trigger))
            good[trigger] = text
    return good


def with_saved(snippets: dict, old_trigger, trigger, text) -> dict:
    """A copy with this snippet saved in place of old_trigger (None for a new
    one), or SnippetError. Only the trigger is tidied; the text stays exact."""
    if not isinstance(trigger, str) or not normalize_trigger(trigger):
        raise SnippetError("a snippet needs a phrase to say")
    if not isinstance(text, str) or not text.strip():
        raise SnippetError("a snippet needs text to paste")
    trigger = " ".join(trigger.split())
    key = normalize_trigger(trigger)
    for other in snippets:
        if other != old_trigger and normalize_trigger(other) == key:
            raise SnippetError(f"“{other}” already uses that phrase")
    if old_trigger not in snippets:
        return {**snippets, trigger: text}
    return {(trigger if name == old_trigger else name): (text if name == old_trigger else saved)
            for name, saved in snippets.items()}


def trigger_warning(trigger: str) -> str | None:
    """Allowed, but worth saying: these can fire when you didn't mean them to."""
    key = normalize_trigger(trigger)
    if key in _NOISE_PHRASES:
        return "Whisper can hear this from a stray noise, so it may paste by accident."
    if " " not in key or sum(c.isalnum() for c in key) < SHORT_TRIGGER_LETTERS:
        return "Very short, so it may be misheard or said by accident."
    return None
