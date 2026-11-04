"""Names and terms worth biasing whisper toward, picked out of on-screen text.
Local, reading nothing but the system dictionary. Only short, name-shaped tokens get out of here, never a run of
the text itself."""
import re

from voisr.ax_context import BROWSERS, TERMINALS, FocusContext
from voisr.common_words import everyday_word_check, is_common_word

DEFAULT_LIMIT = 20
MAX_OPENERS_CHECKED = 20  # each is a search through the system dictionary
SHORT_CLIP_S = 2.0
SHORT_CLIP_TERMS = 5
MOSTLY_TERMS = 0.6
# Quick speech runs near 15 letters a second, and names are harder to rush.
MAX_LETTERS_PER_S = 20
_MAX_TERM = 32
_MAX_MIXED = 16  # a longer run of letters and digits is more likely a key
_STRIP = ".,:;!?()[]{}<>\"'“”‘’`*|"
_ENDS_SENTENCE = ".!?:"

_HANDLE = re.compile(r"[@#][A-Za-z][\w.-]*")
_CAMEL = re.compile(r"[a-z][a-z0-9]*[A-Z][A-Za-z0-9]*")
_PASCAL = re.compile(r"[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*")
_SNAKE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+")
_MIXED = re.compile(r"[A-Za-z0-9]+")
_CAPITALISED = re.compile(r"[A-Z][\w'’-]+")


def _is_term(token: str, sentence_start: bool) -> bool:
    if len(token) < 2 or len(token) > _MAX_TERM:
        return False
    if "://" in token or "/" in token or "\\" in token:
        return False
    if _HANDLE.fullmatch(token):
        return True
    if "@" in token or "#" in token:  # emails and the like
        return False
    digits = sum(c.isdigit() for c in token)
    if digits:
        return (_MIXED.fullmatch(token) is not None
                and any(c.isalpha() for c in token)
                and digits * 2 <= len(token)
                and len(token) <= _MAX_MIXED)
    if _CAMEL.fullmatch(token) or _PASCAL.fullmatch(token) or _SNAKE.fullmatch(token):
        return True
    return (not sentence_start
            and _CAPITALISED.fullmatch(token) is not None
            and not is_common_word(token))


class SentenceOpener(str):
    """A term kept for what the dictionary says of it, not for how it sits in a
    sentence. Alone in a field it is also what a revealed password looks like."""


def is_mixed_term(term: str) -> bool:
    """Letters and digits together: gpt4 and M1, but also what a visible
    password or one-time code looks like, and nothing tells them apart."""
    return any(c.isdigit() for c in term)


def cli_safe_terms(terms) -> list[str]:
    """What may go on whisper-cli's command line, where other processes of this
    user can read it for as long as it runs."""
    return [term for term in terms
            if not is_mixed_term(term) and not isinstance(term, SentenceOpener)]


def _tokens(text: str, sentence_start: bool = True):
    """(token, is first in its sentence) in reading order."""
    for raw in text.split():
        token = raw.strip(_STRIP)
        if token.endswith(("'s", "’s")):
            token = token[:-2]
        yield token, sentence_start
        sentence_start = raw.rstrip(")]}\"'”’")[-1:] in _ENDS_SENTENCE


def _terms_in(text: str, sentence_start: bool = True) -> list[str]:
    return [t for t, start in _tokens(text, sentence_start) if _is_term(t, start)]


def _openers_in(text: str, sentence_start: bool = True) -> list[str]:
    """Capitalised words that only sentence position keeps from being terms: a
    name alone on a line, or one that opens a sentence."""
    return [t for t, start in _tokens(text, sentence_start)
            if start and _is_term(t, False) and not _is_term(t, True)]


def _caret_order(before: str, after: str, pick=_terms_in) -> list[str]:
    """Words around the caret, nearest first, taking turns from each side."""
    left = list(reversed(pick(before)))
    # text after the caret carries on the sentence before it
    tail = before.rstrip()
    right = pick(after, not tail or tail[-1] in _ENDS_SENTENCE)
    merged = []
    for i in range(max(len(left), len(right))):
        merged += left[i:i + 1] + right[i:i + 1]
    return merged


def _in_source_order(focus: FocusContext, pick) -> list[str]:
    ordered = pick(focus.selected_text)
    ordered += _caret_order(focus.before_text, focus.after_text, pick)
    for text in focus.nearby_texts:
        ordered += pick(text)
    return ordered + pick(focus.window_title)


def context_terms(focus: FocusContext | None, limit: int = DEFAULT_LIMIT) -> list[str]:
    """Terms by source, then, with room left, words opening a sentence that the
    system dictionary doesn't have as ordinary words ("Tareq", not "Meanwhile")."""
    if focus is None or focus.secure:
        return []
    # A terminal or a web page is where a code or a generated password sits in
    # plain view, so mixed tokens from there aren't worth having.
    skip_mixed = focus.bundle_id in TERMINALS or focus.bundle_id in BROWSERS
    seen, out = set(), []
    for term in _in_source_order(focus, _terms_in):
        if skip_mixed and is_mixed_term(term):
            continue
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            out.append(term)
        if len(out) >= limit:
            return out
    openers = list(dict.fromkeys(_in_source_order(focus, _openers_in)))
    openers = [word for word in openers if word.casefold() not in seen][:MAX_OPENERS_CHECKED]
    if openers:
        everyday = everyday_word_check()
        for word in openers:
            key = word.casefold()
            if key not in seen and not everyday(word):
                seen.add(key)
                out.append(SentenceOpener(word))
                if len(out) >= limit:
                    break
    return out


def terms_for_clip(terms: list[str], wav_seconds: float) -> list[str]:
    """Whisper can echo a long prompt on a very short clip."""
    return terms[:SHORT_CLIP_TERMS] if wav_seconds < SHORT_CLIP_S else terms


def _mostly_terms_unsupported(raw: str, terms, seconds: float) -> bool:
    """Output made up mostly of context terms that this clip couldn't have held.
    Two rules, so a sentence with a name or two in it never trips them: all of
    a short clip being terms, or three or more terms and most of the output,
    from a short clip or faster than anyone speaks."""
    words = [word.strip(_STRIP).casefold() for word in raw.split()]
    words = [word for word in words if word]
    known = {term.casefold().lstrip("@#") for term in terms}
    matched = sum(word.lstrip("@#") in known for word in words)
    if not words or matched < 2:
        return False
    short = seconds < SHORT_CLIP_S
    if matched == len(words) and short:
        return True
    letters = sum(c.isalnum() for word in words for c in word)
    too_fast = letters > MAX_LETTERS_PER_S * max(seconds, 0.1)
    return matched >= 3 and matched / len(words) >= MOSTLY_TERMS and (short or too_fast)


def prompt_echoed(raw: str, terms, seconds: float | None = None) -> bool:
    """Did whisper recite the glossary instead of the speech? The word itself,
    three context terms in a row in prompt order, or, knowing the clip's length
    in seconds, output that is mostly terms and more than that clip can hold."""
    if re.search(r"\bglossary\s*:", raw, re.IGNORECASE):
        return True
    terms = list(terms)
    for i in range(len(terms) - 2):
        run = r"\W+".join(re.escape(t) for t in terms[i:i + 3])
        if re.search(run, raw, re.IGNORECASE):
            return True
    return seconds is not None and _mostly_terms_unsupported(raw, terms, seconds)


def triggers_recited(raw: str, triggers, other_terms=()) -> bool:
    """Is this the prompt's list of snippet triggers, not speech? The words must
    be wholly used up by two or more entries, at least one a trigger, with or
    without punctuation between them."""
    def words_of(text: str) -> tuple[str, ...]:
        words = (word.strip(_STRIP).casefold() for word in text.split())
        return tuple(word for word in words if word)

    wanted = {words_of(trigger) for trigger in triggers}
    entries = wanted | {words_of(term) for term in other_terms}
    entries.discard(())
    words = words_of(raw)
    if words[:1] == ("glossary",):
        words = words[1:]
    # for each position, what the ways of reaching it have used: (entries so far
    # capped at 2, whether one was a trigger)
    reached = [set() for _ in range(len(words) + 1)]
    reached[0].add((0, False))
    for at, states in enumerate(reached):
        for entry in entries:
            if states and words[at:at + len(entry)] == entry:
                reached[at + len(entry)] |= {
                    (min(count + 1, 2), used or entry in wanted) for count, used in states}
    return (2, True) in reached[len(words)]
