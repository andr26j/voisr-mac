"""Saved text that a whole dictation can stand in for: {trigger: text} in
config.json. A trigger keeps the spelling it was saved with; the text is the
user's and is never altered. Nothing here logs either of them."""
import re
from dataclasses import dataclass

from voisr.cleanup import NON_SPEECH_RE

_APOSTROPHES = str.maketrans({"’": "'", "‘": "'"})
_OUTER_PUNCTUATION = re.compile(r"^[\W_]+|[\W_]+$")
# Triggers are too short to survive Whisper without a hint: 81-84% came out
# exact without, 100% with (scripts/bench_triggers.py, synthetic voices).
MAX_TRIGGER_HINTS = 20      # the rest of the prompt is the Dictionary's
MAX_TRIGGER_HINT_CHARS = 60
MAX_TRIGGER_HINTS_TOTAL_CHARS = 250  # of the prompt's 600, so the Dictionary keeps room
# Trimmed clips measured, say at 100 words a minute, the slowest tried: 2.1 s for
# up to 19 characters, 4.2 s for 49. These allow about twice that.
HINT_CLIP_BASE_S = 3.0
HINT_CLIP_PER_CHAR_S = 0.1
# Key thumps can pass the speech gate, and with triggers in the prompt Whisper
# can answer them with one. Speech holds its level; a thump falls away from its
# peak. 240 ms within 15 dB of the clip's peak: 0/250 made-up tap-then-holds
# reach it, 826/840 say clips do. A clip held back pastes as plain dictation.
MIN_SUSTAINED_SPEECH_MS = 240
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


def triggers_for_clip(hints, seconds: float) -> list[str]:
    """The hints a clip of this length could be saying. Longer clips can't match
    one, so ordinary dictation isn't biased. Unknown length (-1) gets none."""
    return [trigger for trigger in hints
            if 0 <= seconds <= HINT_CLIP_BASE_S + HINT_CLIP_PER_CHAR_S * len(trigger)]


def find_spoken_snippet(raw: str, snippets, span) -> Snippet | None:
    """find_snippet, only with evidence of real speech from the gate's reading of
    this clip, taken before it was trimmed. No reading is no evidence."""
    if span is None or span.sustained_ms < MIN_SUSTAINED_SPEECH_MS:
        return None
    return find_snippet(raw, snippets)


def trigger_hints(snippets) -> list[str]:
    """Triggers for the Whisper prompt, the newest last and so kept first. Only
    triggers: the saved text never goes near a prompt."""
    hints, used = [], 0
    for trigger in reversed(list(snippets or {})):
        cost = len(trigger) + 2  # ", "
        if (len(trigger) <= MAX_TRIGGER_HINT_CHARS and len(hints) < MAX_TRIGGER_HINTS
                and used + cost <= MAX_TRIGGER_HINTS_TOTAL_CHARS):
            hints.append(trigger)
            used += cost
    hints.reverse()
    return hints


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


def tidy_trigger(trigger: str) -> str:
    return " ".join(trigger.split())


def with_saved(snippets: dict, old_trigger, trigger, text) -> dict:
    """A copy with this snippet saved in place of old_trigger (None for a new
    one), or SnippetError. Only the trigger is tidied; the text stays exact."""
    if not isinstance(trigger, str) or not normalize_trigger(trigger):
        raise SnippetError("a snippet needs a phrase to say")
    if not isinstance(text, str) or not text.strip():
        raise SnippetError("a snippet needs text to paste")
    trigger = tidy_trigger(trigger)
    key = normalize_trigger(trigger)
    for other in snippets:
        if other != old_trigger and normalize_trigger(other) == key:
            raise SnippetError(f"“{other}” already uses that phrase")
    if not isinstance(old_trigger, str) or old_trigger not in snippets:
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
