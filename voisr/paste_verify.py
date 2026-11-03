"""Did the paste land? Compares the field before and after, without ever
sending anything. Holds no text beyond the call."""
import re
from dataclasses import dataclass

from voisr.ax_context import FocusContext, MAX_VALUE_CHARS

# Added to the clipboard restore at most; the spec's C.
VERIFY_BUDGET_S = 0.15
MIN_OLD_CLIPBOARD = 3  # a short old clipboard turns up in any text by chance
# How much a field may grow, against the pasted text, and still be that text
# after an app reformatted it.
MIN_GROWTH, MAX_GROWTH = 0.5, 2.0

_LOOKALIKES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


@dataclass(frozen=True)
class Verdict:
    status: str  # verified | not_found | stale_clipboard | skipped
    reason: str | None = None

    @property
    def missed(self) -> bool:
        """The dictation didn't land: leave it on the clipboard."""
        return self.status in ("not_found", "stale_clipboard")

    def fields(self) -> dict:
        details = {"delivery": self.status}
        if self.reason:
            details["delivery_reason"] = self.reason
        return details


def skipped(reason: str) -> Verdict:
    return Verdict("skipped", reason)


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_LOOKALIKES)).strip()


def _count(haystack: str, needle: str) -> int:
    return haystack.count(needle) if needle else 0


def _squeeze(normal: str) -> str:
    """No case and no whitespace: what is left after an app has reformatted."""
    return "".join(normal.split()).casefold()


def _grew_by_about(before: str, after: str, text: str) -> bool:
    grown = len(after) - len(before)
    return len(text) * MIN_GROWTH <= grown <= len(text) * MAX_GROWTH


def verify_paste(before: str | None, after: str | None, text: str,
                 old_clipboard: str | None) -> Verdict:
    """None means unreadable, which is not the same as empty. An empty `after`
    proves nothing either: Electron apps report "" for fields that hold text,
    and a message can be sent inside the grace."""
    if before is None or after is None:
        return skipped("unreadable")
    if len(before) > MAX_VALUE_CHARS or len(after) > MAX_VALUE_CHARS:
        return skipped("too_long")
    before_n, after_n, wanted = _normal(before), _normal(after), _normal(text)
    if not after_n:
        return skipped("empty_field")
    if _count(after_n, wanted) > _count(before_n, wanted):
        return Verdict("verified")
    old = _normal(old_clipboard) if old_clipboard else ""
    # Old clipboard text inside the pasted text (likely in a long snippet) proves
    # nothing: the paste itself would add it.
    if (len(old) >= MIN_OLD_CLIPBOARD and old not in wanted
            and _count(after_n, old) > _count(before_n, old)):
        return Verdict("stale_clipboard")
    # Apps strip newlines, change case and auto-format. Calling that a miss
    # would lose the user's clipboard and have them paste twice, so only a field
    # that clearly didn't change is one.
    before_s, after_s, wanted_s = _squeeze(before_n), _squeeze(after_n), _squeeze(wanted)
    if _count(after_s, wanted_s) > _count(before_s, wanted_s):
        return skipped("transformed")
    if after_s == before_s:
        return Verdict("not_found")
    if _grew_by_about(before_s, after_s, wanted_s):
        return skipped("transformed")
    return skipped("field_changed")


def check_delivery(before: FocusContext | None, after: FocusContext | None,
                   text: str, old_clipboard: str | None) -> Verdict:
    """The verdict for a release capture and the read after the grace."""
    if before is None:
        return skipped("no_baseline")
    if after is None:
        return skipped("timeout")
    if before.secure or after.secure:
        return skipped("secure")
    if after.reason in ("no_focused_element", "not_editable", "too_long", "timeout"):
        return skipped(after.reason)
    if not after.readable or not before.readable:
        return skipped("too_long" if before.reason == "too_long" else "unreadable")
    if after.element_id != before.element_id:
        return skipped("focus_changed")
    verdict = verify_paste(before.value, after.value, text, old_clipboard)
    if verdict.missed and not after.trust_absence:
        return skipped("unverifiable_app")
    return verdict
