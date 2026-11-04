"""Snippets as a dictation takes them, through the Api with a fake AX adapter:
exact text, what History keeps, and that learning never sees them."""
import json

import pytest

from tests.fake_ax import FakeAX, FakeElement, textbox
from tests.test_snippets_guard import ClipRecorder, spoken
from tests.test_context_api import (correct, dictate, make_api, no_system_dictionary,  # noqa: F401
                                    run_windows, settle, verifying_paste)

SpeechRecorder = type("SpeechRecorder", (ClipRecorder,), {"samples": spoken()})
SECRET = "andre@example.com"
LONG = ("Please review this:\n\n  - um, the diff\n  - the tests\n\n" + "Keep it short. " * 400
        + "\n")


def snippet_api(tmp_path, spoken="My email.", ax=None, snippets=None, **kwargs):
    pasted = []
    kwargs.setdefault("paste_fn", lambda text: pasted.append(text) or True)
    said = [spoken]
    api = make_api(tmp_path, ax, transcribe_fn=lambda wav: said[0], **kwargs)
    api._recordings._recorder_factory = SpeechRecorder
    api.set_config({"snippets": {"my email": SECRET} if snippets is None else snippets})
    return api, pasted, said


def disk(tmp_path):
    return (tmp_path / "history.jsonl").read_text()


def test_the_saved_text_is_pasted_exactly(tmp_path):
    api, pasted, _ = snippet_api(tmp_path, "Review prompt.", snippets={"review prompt": LONG})
    api.set_config({"dictionary": {"diff": "CHANGED", "keep": "CHANGED"}})
    out = dictate(api)
    assert pasted == [LONG]
    assert out["pasted"] is True and "error" not in out
    api.close()


def test_saved_text_keeps_its_own_outer_spaces_and_lower_case(tmp_path):
    api, pasted, _ = snippet_api(tmp_path, snippets={"my email": "  lower case, um , ok \n"})
    dictate(api)
    assert pasted == ["  lower case, um , ok \n"]
    api.close()


def test_a_sentence_holding_the_trigger_stays_as_spoken(tmp_path):
    api, pasted, _ = snippet_api(tmp_path, "I checked my email.")
    dictate(api)
    assert pasted == ["I checked my email."]
    item = api.get_history()[0]
    assert item["text"] == "I checked my email." and "snippet" not in item
    api.close()


def test_a_near_miss_pastes_as_normal_dictation(tmp_path):
    api, pasted, _ = snippet_api(tmp_path, "My e-mail.")
    dictate(api)
    assert pasted == ["My e-mail."] and SECRET not in disk(tmp_path)
    api.close()


def test_snippets_beat_the_dictionary_on_a_whole_match(tmp_path):
    api, pasted, _ = snippet_api(tmp_path)
    api.set_config({"dictionary": {"my email": "DICTIONARY", "email": "DICTIONARY"}})
    dictate(api)
    assert pasted == [SECRET]
    api.close()


def test_the_match_is_on_what_was_heard_not_a_dictionary_replacement(tmp_path):
    api, pasted, _ = snippet_api(tmp_path, "My male.")
    api.set_config({"dictionary": {"my male": "my email"}})
    dictate(api)
    assert pasted == ["My email."]
    api.close()


def test_a_dictionary_entry_fires_no_used_count_on_a_snippet(tmp_path):
    api, _, _ = snippet_api(tmp_path)
    api.set_config({"dictionary": {"email": "Email"}})
    dictate(api)
    assert api.get_suggestions()["used"] == {}
    api.close()


def test_no_leading_space_after_a_recent_dictation(tmp_path):
    api, pasted, said = snippet_api(tmp_path, "Send it to")
    dictate(api)
    said[0] = "My email."
    dictate(api)
    assert pasted == ["Send it to", SECRET]
    said[0] = "Thanks."
    dictate(api)
    assert pasted[-1] == " Thanks.", "a dictation after a snippet joins as usual"
    api.close()


def test_with_paste_off_the_clipboard_gets_it_exactly(tmp_path):
    written = []
    api, pasted, _ = snippet_api(tmp_path, snippets={"my email": LONG})
    api._clipboard_fn = written.append
    out = dictate(api, paste=False)
    assert pasted == [] and written == [LONG] and out["copied"] is True
    api.close()


# ---- History


def test_the_row_holds_the_trigger_and_a_label_not_the_text(tmp_path):
    api, _, _ = snippet_api(tmp_path, "MY EMAIL!", snippets={"My Email": SECRET})
    dictate(api)
    item = api.get_history()[0]
    assert item["text"] == "My Email" and item["snippet"] is True and item["words"] == 2
    assert SECRET not in disk(tmp_path)
    assert SECRET not in json.dumps(api.get_stats())
    for fmt in ("json", "md", "txt"):
        assert SECRET not in api.export_history(fmt)["text"]
    api.close()


def test_the_label_survives_delete_and_undo(tmp_path):
    api, _, _ = snippet_api(tmp_path)
    dictate(api)
    item = api.get_history()[0]
    api.delete_history_item(item["id"])
    api.restore_history_item(item)
    assert api.get_history()[0]["snippet"] is True
    api.close()


def test_nothing_logs_the_text(tmp_path, capsys):
    box = textbox("start ")
    api, _, _ = snippet_api(tmp_path, ax=FakeAX(box), paste_fn=verifying_paste(box))
    dictate(api)
    settle(api)
    api.close()
    captured = capsys.readouterr()
    assert SECRET not in captured.out and SECRET not in captured.err


def test_only_the_config_holds_the_text(tmp_path):
    box = textbox("start ")
    api, _, _ = snippet_api(tmp_path, ax=FakeAX(box), paste_fn=verifying_paste(box))
    dictate(api)
    settle(api)
    api.close()
    holding = [p.name for p in tmp_path.rglob("*") if p.is_file() and SECRET in p.read_text()]
    assert holding == ["config.json"]


# ---- no learning


def test_editing_a_pasted_snippet_teaches_nothing(tmp_path, no_system_dictionary):
    box = textbox("start ")
    api, _, _ = snippet_api(tmp_path, "my email", ax=FakeAX(box), paste_fn=verifying_paste(box),
                            snippets={"my email": "voiser is great"})
    assert dictate(api)["delivery"] == "verified"
    assert api._edit_watcher._windows == [], "no window opens on a snippet"
    correct(api, box)
    assert api.get_suggestions()["pending"] == []
    assert not (tmp_path / "suggestions.json").exists() or "Voisr" not in (
        tmp_path / "suggestions.json").read_text()
    assert "words_corrected" not in api.get_history()[0]
    api.close()


def test_a_snippet_ends_the_last_dictations_window_before_it_lands(tmp_path, no_system_dictionary):
    box = textbox("start ")
    seen_open = []
    paste = verifying_paste(box)

    def paste_fn(text, on_sent=None, verifier=None):
        seen_open.append(list(api._edit_watcher._windows))
        return paste(text, on_sent, verifier)

    api, _, said = snippet_api(tmp_path, "hello there", ax=FakeAX(box), paste_fn=paste_fn,
                               snippets={"my email": " and voisr@example.com is great"})
    dictate(api)
    assert len(api._edit_watcher._windows) == 1
    said[0] = "My email."
    dictate(api)
    assert seen_open[1] == [], "ended before the paste, so no read can see the text"
    run_windows(api)
    assert api.get_suggestions()["pending"] == []
    first = api.get_history()[1]
    assert first["edit_end_reason"] == "next_paste" and first["words_corrected"] == 0
    api.close()


def test_a_fix_made_before_the_snippet_still_counts(tmp_path, no_system_dictionary):
    box = textbox("start ")
    api, _, said = snippet_api(tmp_path, "voiser is great", ax=FakeAX(box),
                               paste_fn=verifying_paste(box))
    dictate(api)
    box.attrs["AXValue"] = box.attrs["AXValue"].replace("Voiser", "Voisr")
    box.attrs["AXSelectedTextRange"] = (len(box.attrs["AXValue"]), 0)
    said[0] = "My email."
    dictate(api)
    run_windows(api)
    assert [(p["heard"], p["written"]) for p in api.get_suggestions()["pending"]] == [
        ("Voiser", "Voisr")]
    assert SECRET not in (tmp_path / "suggestions.json").read_text()
    api.close()


def test_no_release_capture_ends_every_window_too(tmp_path):
    box = textbox("start ")
    ax = FakeAX(box)
    api, _, said = snippet_api(tmp_path, "hello there", ax=ax, paste_fn=verifying_paste(box))
    dictate(api)
    assert len(api._edit_watcher._windows) == 1
    api._edit_watcher.end_for_snippet(None)
    assert api._edit_watcher._windows == []
    api.close()


# ---- paste verification


def test_long_multi_line_text_verifies_with_no_copied_message(tmp_path):
    box = textbox("start ")
    api, _, _ = snippet_api(tmp_path, ax=FakeAX(box), paste_fn=verifying_paste(box),
                            snippets={"my email": LONG})
    out = dictate(api)
    assert out["delivery"] == "verified" and "manual_paste" not in out and out["pasted"] is True
    api.close()


def test_text_over_the_read_limit_is_skipped_not_called_a_miss(tmp_path):
    huge = "word " * 5000
    box = textbox("start ")
    box.attrs["AXNumberOfCharacters"] = lambda: len(box.attrs["AXValue"])
    api, _, _ = snippet_api(tmp_path, ax=FakeAX(box), paste_fn=verifying_paste(box),
                            snippets={"my email": huge})
    out = dictate(api)
    assert out["delivery"] == "skipped" and "manual_paste" not in out and out["pasted"] is True
    api.close()


# ---- password fields


def test_a_password_field_gets_the_paste_and_nothing_is_read_or_named(tmp_path):
    secure = FakeElement(AXRole="AXTextField", AXSubrole="AXSecureTextField")
    pasted = []
    ax = FakeAX(secure)

    def paste_fn(text, on_sent=None, verifier=None):
        pasted.append(text)
        if verifier:
            verifier("OLD COPY")
        return True

    api, _, _ = snippet_api(tmp_path, ax=ax, paste_fn=paste_fn)
    out = dictate(api)
    assert pasted == [SECRET] and out["delivery"] == "skipped" and "manual_paste" not in out
    item = api.get_history()[0]
    assert item["text"] == "my email" and item["delivery_reason"] == "secure"
    assert "app" not in item
    assert SECRET not in disk(tmp_path)
    assert (secure, "AXValue") not in ax.calls
    assert api._edit_watcher._windows == []
    api.close()


# ---- the page's bridge


def test_saving_and_deleting_through_the_bridge(tmp_path):
    api, _, _ = snippet_api(tmp_path, snippets={})
    api.set_config({"dictionary": {"a": "b"}})
    assert api.save_snippet(None, "  my   email ", "  exact\n") == {
        "ok": True, "trigger": "my email", "warning": None}
    assert api.get_config()["snippets"] == {"my email": "  exact\n"}
    assert api.save_snippet("my email", "work email", "new")["ok"] is True
    assert api.get_snippets() == [{"trigger": "work email", "text": "new", "warning": None}]
    assert api.delete_snippet("work email") == {"ok": True}
    assert api.delete_snippet("work email") == {"ok": True}
    assert api.get_snippets() == []
    assert api.get_config()["dictionary"] == {"a": "b"}
    api.close()


@pytest.mark.parametrize("trigger,text", [
    ("My Email.", "duplicate"), ("MY  EMAIL", "duplicate"), ("", "x"), ("  ", "x"), ("...", "x"),
    ("other", ""), ("other", " \n "), (None, "x"), ("other", None), (3, 4),
])
def test_the_bridge_refuses_and_changes_nothing(tmp_path, trigger, text):
    api, _, _ = snippet_api(tmp_path)
    assert "error" in api.save_snippet(None, trigger, text)
    assert api.get_config()["snippets"] == {"my email": SECRET}
    api.close()


def test_a_short_trigger_saves_with_a_warning(tmp_path):
    api, _, _ = snippet_api(tmp_path, snippets={})
    out = api.save_snippet(None, "email", "x")
    assert out["ok"] is True and "short" in out["warning"]
    assert api.get_snippets()[0]["warning"] == out["warning"]
    api.close()


def test_a_bad_value_through_set_config_changes_nothing(tmp_path):
    api, _, _ = snippet_api(tmp_path)
    assert "error" in api.set_config({"snippets": {"my email": ""}, "paste": False})
    cfg = api.get_config()
    assert cfg["snippets"] == {"my email": SECRET} and cfg["paste"] is True
    api.close()


def test_whisper_gets_the_triggers_and_never_the_text(tmp_path, monkeypatch):
    seen = {}

    def routed(wav, model, prompt=None, server=None, unload="min5", cli_prompt=None):
        seen.update(prompt=prompt, cli_prompt=cli_prompt)
        return "My email.", {"mode": "cli", "timings": {}}

    monkeypatch.setattr("voisr.transcriber.transcribe_routed", routed)
    pasted = []
    from voisr.history import HistoryStore
    from voisr.ui import Api
    api = Api(config_path=tmp_path / "config.json", history=HistoryStore(tmp_path / "h.jsonl"),
              recorder_factory=SpeechRecorder, clipboard_fn=lambda text: None,
              paste_fn=lambda text: pasted.append(text) or True)
    api.set_config({"snippets": {"my email": SECRET}})
    dictate(api)
    assert seen == {"prompt": "Glossary: my email.", "cli_prompt": "Glossary: my email."}
    assert pasted == [SECRET]
    api.close()


# ---- the field that gets the paste


def test_a_field_clicked_during_transcription_cannot_read_the_snippet_as_an_edit(tmp_path):
    first, second = textbox("start "), textbox("other ")
    ax = FakeAX(first)
    open_at_paste = []
    land = verifying_paste(first)

    def paste_fn(text, on_sent=None, verifier=None):
        open_at_paste.append(list(api._edit_watcher._windows))
        return land(text, on_sent, verifier)

    api, _, said = snippet_api(tmp_path, "hello there", ax=ax, paste_fn=paste_fn)
    dictate(api)
    assert len(api._edit_watcher._windows) == 1
    said[0] = "My email."
    # key released in the other field, then a click back before the paste
    ax.focused = second
    api.start_recording(prewarm=False)
    api.prewarm_now()
    settle(api)
    future = api._release_recording(paste=True)
    future.result()
    assert open_at_paste[-1] == [], "every window ends before the paste"
    api.close()


# ---- the joining space


@pytest.mark.parametrize("before,saved,pasted_as", [
    ("Send it to", SECRET, f" {SECRET}"),
    ("Email:", SECRET, f" {SECRET}"),
    ("Dear Sam, To:", SECRET, f" {SECRET}"),
    ("Send it to.", SECRET, f" {SECRET}"),
    ("Done!", SECRET, f" {SECRET}"),
    ("(see below)", SECRET, f" {SECRET}"),
    ("“Hello”", SECRET, f" {SECRET}"),
    ("Room 12", SECRET, f" {SECRET}"),
    ("mailto:", SECRET, SECRET),
    ("https:", SECRET, SECRET),
    ("email:", SECRET, SECRET),
    ("x=", SECRET, SECRET),
    ("name@", SECRET, SECRET),
    ("#", SECRET, SECRET),
    ("path/", SECRET, SECRET),
    ("a-", SECRET, SECRET),
    ("_", SECRET, SECRET),
    ("", SECRET, SECRET),
    ("Send it to ", SECRET, SECRET),
    ("Line one\n", SECRET, SECRET),
    ("(", SECRET, SECRET),
    ("Send it to", f"\n{SECRET}", f"\n{SECRET}"),
    ("Send it to", f", {SECRET}", f", {SECRET}"),
])
def test_a_space_only_when_the_text_before_the_cursor_needs_it(tmp_path, before, saved, pasted_as):
    box = textbox(before)
    pasted = []
    land = verifying_paste(box)

    def paste_fn(text, on_sent=None, verifier=None):
        pasted.append(text)
        return land(text, on_sent, verifier)

    api, _, _ = snippet_api(tmp_path, ax=FakeAX(box), paste_fn=paste_fn, snippets={"my email": saved})
    out = dictate(api)
    assert pasted == [pasted_as] and out["delivery"] == "verified"
    api.close()


def test_a_space_goes_by_the_field_at_paste_time(tmp_path):
    empty = textbox("")
    pasted = []
    ax = FakeAX(textbox("Send it to"))
    land = verifying_paste(empty)

    def paste_fn(text, on_sent=None, verifier=None):
        pasted.append(text)
        return land(text, on_sent, verifier)

    api, _, _ = snippet_api(tmp_path, ax=ax, paste_fn=paste_fn)
    api.start_recording(prewarm=False)
    api.prewarm_now()
    settle(api)
    future = api._release_recording(paste=True)
    ax.focused = empty  # may land before or after the release capture
    future.result()
    assert pasted == [SECRET]
    api.close()


@pytest.mark.parametrize("ax", [
    lambda: FakeAX(FakeElement(AXRole="AXTextField", AXSubrole="AXSecureTextField")),
    lambda: FakeAX(textbox("Send it to", role="AXButton")),
    lambda: FakeAX(FakeElement(AXRole="AXTextArea", AXValue="Send it to")),  # caret unknown
])
def test_no_space_when_it_is_not_known(tmp_path, ax):
    pasted = []

    def paste_fn(text, on_sent=None, verifier=None):
        pasted.append(text)
        if verifier:
            verifier("OLD COPY")
        return True

    api, _, _ = snippet_api(tmp_path, ax=ax(), paste_fn=paste_fn)
    dictate(api)
    assert pasted == [SECRET]
    api.close()


def test_no_space_with_screen_context_off_and_nothing_read(tmp_path):
    box = textbox("Send it to")
    ax = FakeAX(box)
    api, pasted, _ = snippet_api(tmp_path, ax=ax)
    api.set_config({"use_screen_context": False})
    ax.calls.clear()
    dictate(api)
    assert pasted == [SECRET] and ax.text_reads() == []
    api.close()


def test_the_row_and_the_check_ignore_the_added_space(tmp_path):
    box = textbox("Send it to")
    api, _, _ = snippet_api(tmp_path, ax=FakeAX(box), paste_fn=verifying_paste(box))
    out = dictate(api)
    assert out["delivery"] == "verified" and out["text"] == SECRET
    assert api.get_history()[0]["text"] == "my email"
    api.close()


# ---- triggers that get no hint


def test_a_trigger_with_no_hint_is_warned_on_its_row(tmp_path):
    api, _, _ = snippet_api(tmp_path, snippets={})
    long_one = "please paste the whole of my standard legal disclaimer right here"
    assert "hint" in api.save_snippet(None, long_one, "x")["warning"]
    for i in range(21):
        api.save_snippet(None, f"saved phrase {i:02}", "x")
    rows = {row["trigger"]: row["warning"] for row in api.get_snippets()}
    assert "hint" in rows[long_one]
    assert "hint" in rows["saved phrase 00"], "pushed out by newer ones"
    assert rows["saved phrase 20"] is None
    api.close()


def test_a_short_trigger_with_no_hint_gets_both_warnings(tmp_path):
    many = {"email": "x", **{f"phrase {i:02}": "x" for i in range(20)}}
    api, _, _ = snippet_api(tmp_path, snippets=many)
    warning = {row["trigger"]: row["warning"] for row in api.get_snippets()}["email"]
    assert "short" in warning and "hint" in warning
    api.close()
