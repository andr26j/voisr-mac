"""Snippets as a dictation takes them, through the Api with a fake AX adapter:
exact text, what History keeps, and that learning never sees them."""
import json

import pytest

from tests.fake_ax import FakeAX, FakeElement, textbox
from tests.test_context_api import (correct, dictate, make_api, no_system_dictionary,  # noqa: F401
                                    run_windows, settle, verifying_paste)

SECRET = "andre@example.com"
LONG = ("Please review this:\n\n  - um, the diff\n  - the tests\n\n" + "Keep it short. " * 400
        + "\n")


@pytest.fixture(autouse=True)
def no_speech_gate(monkeypatch):
    monkeypatch.setattr("voisr.speech_gate.gate_wav", lambda wav: None)


def snippet_api(tmp_path, spoken="My email.", ax=None, snippets=None, **kwargs):
    pasted = []
    kwargs.setdefault("paste_fn", lambda text: pasted.append(text) or True)
    said = [spoken]
    api = make_api(tmp_path, ax, transcribe_fn=lambda wav: said[0], **kwargs)
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


def test_a_window_in_another_field_is_left_alone(tmp_path):
    first, second = textbox("start "), textbox("other ")
    ax = FakeAX(first)
    api, _, said = snippet_api(tmp_path, "hello there", ax=ax, paste_fn=verifying_paste(first))
    dictate(api)
    assert len(api._edit_watcher._windows) == 1
    ax.focused = second
    api._paste_fn = verifying_paste(second)
    said[0] = "My email."
    dictate(api)
    assert len(api._edit_watcher._windows) == 1
    api.close()


def test_not_knowing_the_field_ends_every_window(tmp_path):
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
    from tests.test_context_api import FakeRecorder
    api = Api(config_path=tmp_path / "config.json", history=HistoryStore(tmp_path / "h.jsonl"),
              recorder_factory=FakeRecorder, clipboard_fn=lambda text: None,
              paste_fn=lambda text: pasted.append(text) or True)
    api.set_config({"snippets": {"my email": SECRET}})
    dictate(api)
    assert seen == {"prompt": "Glossary: my email.", "cli_prompt": "Glossary: my email."}
    assert pasted == [SECRET]
    api.close()
