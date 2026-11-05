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
# can answer them with one. See speech_gate._sustained_ms and the design doc for
# what 240 ms was measured against. A clip held back pastes as plain dictation.
# Real one-word clips read 160-360 ms, so many paste as words; the design doc says
# why this isn't lower.
MIN_SUSTAINED_SPEECH_MS = 240
_SPACE_AFTER = ".,;!?)]}”’"
# "To:" and "Email:" are labels; "mailto:" and "https:" run on. A lower-case word
# could be either, and when in doubt there is no space.
_CAPITALISED_LABEL = re.compile(r"(?:^|\s)[A-Z][\w-]*:\Z")
_NO_SPACE_BEFORE = ",.;:!?)]}>”’"
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


def has_speech_evidence(span) -> bool:
    """Did the gate's reading of this clip, taken before it was trimmed, find
    real speech? No reading is no evidence."""
    return span is not None and span.sustained_ms >= MIN_SUSTAINED_SPEECH_MS


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


def needs_leading_space(focus, text: str) -> bool:
    """After words, a snippet needs a space. It can't be taken for granted, as in
    an empty field it could get an email address refused, so no space unless the
    field was read at the caret and shows a word or sentence has just ended.
    `focus`: a read just before the paste, or None."""
    if (focus is None or focus.secure or not focus.readable or not focus.caret_known
            or not focus.before_text or not text
            or text[0].isspace() or text[0] in _NO_SPACE_BEFORE):
        return False
    last = focus.before_text[-1]
    return last.isalnum() or last in _SPACE_AFTER or _CAPITALISED_LABEL.search(
        focus.before_text) is not None


def dictation_wants_space(focus, text: str, recent: bool, last_app) -> bool:
    """A dictation goes by the field where it can be read, the same as a snippet.
    Where it can't, `recent` says the last paste was moments ago with no typing or
    click since, and that only counts in the same app. `focus`: a read just before
    the paste, or None. Browsers, Electron apps and terminals can report an empty
    field that isn't, so an empty read only counts where absence can be trusted."""
    if not text or text[0].isspace() or text[0] in _NO_SPACE_BEFORE:
        return False
    if focus is not None:
        if focus.secure:
            return False
        if focus.readable and focus.caret_known and (focus.before_text or focus.trust_absence):
            # inside a word, a space would split it
            return not focus.after_text[:1].isalnum() and needs_leading_space(focus, text)
        if last_app and focus.bundle_id and focus.bundle_id != last_app:
            return False
    return bool(recent)


def snippet_warnings(snippets: dict) -> dict:
    """{trigger: what to say on its row, or None}. Hints are shared, so saving
    one snippet can cost another its hint."""
    hinted = set(trigger_hints(snippets))
    warnings = {}
    for trigger in snippets:
        parts = [trigger_warning(trigger)]
        if trigger not in hinted:
            parts.append("Gets no hint, as it is too long or there are too many, "
                         "so it is more likely to be misheard.")
        warnings[trigger] = " ".join(part for part in parts if part) or None
    return warnings


def trigger_warning(trigger: str) -> str | None:
    """Allowed, but worth saying: these can fire when you didn't mean them to."""
    key = normalize_trigger(trigger)
    if key in _NOISE_PHRASES:
        also = "" if " " in key else " Said on its own, it may also paste as plain words."
        return "Whisper can hear this from a stray noise, so it may paste by accident." + also
    if " " not in key or sum(c.isalnum() for c in key) < SHORT_TRIGGER_LETTERS:
        return "Very short, so it may be misheard, said by accident, or paste as plain words."
    return None
