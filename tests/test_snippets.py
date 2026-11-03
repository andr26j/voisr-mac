"""Matching a whole dictation to a saved snippet, and what may be saved."""
import json

import pytest

from voisr.app import DEFAULT_CONFIG, config_errors, load_config
from voisr.snippets import (MAX_TRIGGER_HINTS, SnippetError, find_snippet, is_valid,
                            normalize_trigger, salvage, trigger_hints, trigger_warning,
                            with_saved)
from voisr.ui import GLOSSARY_MAX_CHARS, GLOSSARY_MAX_TERMS, glossary_prompt

SAVED = {"my email": "andre@example.com", "Review Prompt": "Please review:\n\n- this\n"}


@pytest.mark.parametrize("raw", [
    "my email", "My email.", "MY EMAIL!", " My email? ", '"My email."', "(My email)",
    "My  email.", "My\nemail.", "[BLANK_AUDIO] My email.", "My email. [ Silence ]",
])
def test_capitals_spacing_and_outer_punctuation_do_not_matter(raw):
    assert find_snippet(raw, SAVED).text == "andre@example.com"


@pytest.mark.parametrize("raw", [
    "I checked my email.", "My email is here.", "my emails", "My e-mail.", "My, email.",
    "email", "Um, my email.", "My email. My email.", "", "   ", "[BLANK_AUDIO]", "...",
])
def test_anything_else_is_ordinary_dictation(raw):
    assert find_snippet(raw, SAVED) is None


def test_the_match_keeps_the_trigger_as_it_was_saved():
    assert find_snippet("review prompt.", SAVED).trigger == "Review Prompt"


def test_no_snippets_means_no_match():
    assert find_snippet("my email", {}) is None
    assert find_snippet("my email", None) is None


def test_a_curly_apostrophe_in_a_trigger_matches_whisper_straight_one():
    assert find_snippet("Mum's address.", {"mum’s address": "1 Home St"}).text == "1 Home St"


def test_inner_punctuation_is_kept():
    assert normalize_trigger("  E-Mail, Please!! ") == "e-mail, please"


@pytest.mark.parametrize("trigger", ["", "   ", "...", "?!", "_"])
def test_a_trigger_with_nothing_to_say_is_refused(trigger):
    with pytest.raises(SnippetError):
        with_saved({}, None, trigger, "text")


@pytest.mark.parametrize("text", ["", "  \n\t "])
def test_empty_text_is_refused(text):
    with pytest.raises(SnippetError):
        with_saved({}, None, "my email", text)


@pytest.mark.parametrize("trigger", ["my email", "MY EMAIL", "My email.", "  my   email "])
def test_a_duplicate_after_normalising_is_refused(trigger):
    with pytest.raises(SnippetError, match="my email"):
        with_saved(SAVED, None, trigger, "other")


def test_the_text_is_saved_exactly_and_the_trigger_tidied():
    text = "  Dear team,\n\n\tum, thanks  \n"
    assert with_saved({}, None, "  my   sign off ", text) == {"my sign off": text}


def test_editing_keeps_the_order_and_may_recase_its_own_trigger():
    saved = with_saved(SAVED, "my email", "My Email", "new")
    assert list(saved) == ["My Email", "Review Prompt"]
    assert saved["My Email"] == "new"
    assert "my email" not in saved


def test_editing_into_another_snippets_trigger_is_refused():
    with pytest.raises(SnippetError):
        with_saved(SAVED, "my email", "review prompt", "x")


def test_saving_does_not_change_what_it_was_given():
    before = dict(SAVED)
    with_saved(SAVED, "my email", "work email", "x")
    assert SAVED == before


@pytest.mark.parametrize("trigger", ["email", "my id", "thank you", "Thanks."])
def test_short_or_noise_like_triggers_get_a_warning(trigger):
    assert trigger_warning(trigger)


@pytest.mark.parametrize("trigger", ["my email", "review prompt", "my phone number"])
def test_ordinary_triggers_get_none(trigger):
    assert trigger_warning(trigger) is None


# ---- config


def test_the_default_is_no_snippets_and_a_good_value_is_accepted():
    assert DEFAULT_CONFIG["snippets"] == {}
    assert config_errors({"snippets": SAVED}) == []


@pytest.mark.parametrize("bad", [
    "text", ["my email"], 3, None, {"": "x"}, {"...": "x"}, {"my email": ""},
    {"my email": 3}, {"my email": "a", "My Email.": "b"},
])
def test_the_bridge_refuses_a_bad_value(bad):
    assert config_errors({"snippets": bad}) == ["invalid value for snippets"]
    assert not is_valid(bad)


@pytest.mark.parametrize("bad", ["text", ["my email"], 3, None])
def test_a_bad_value_on_disk_costs_only_the_snippets(tmp_path, bad):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"snippets": bad, "dictionary": {"a": "b"}, "paste": False}))
    cfg = load_config(path)
    assert cfg["snippets"] == {}
    assert cfg["dictionary"] == {"a": "b"} and cfg["paste"] is False


def test_bad_entries_on_disk_go_and_good_ones_stay(tmp_path, capsys):
    secret = "secret-text-on-disk"
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"snippets": {
        "my email": secret, "My Email.": "duplicate", "": "no trigger", "no text": "",
        "bad text": 3, "review prompt": "fine"}, "dictionary": {"a": "b"}}))
    cfg = load_config(path)
    assert cfg["snippets"] == {"my email": secret, "review prompt": "fine"}
    assert cfg["dictionary"] == {"a": "b"}
    assert secret not in capsys.readouterr().out  # the warning names no text


def test_salvage_keeps_the_entry_a_match_would_have_used():
    both = {"my email": "first", "MY EMAIL": "second"}
    assert find_snippet("my email", both).text == salvage(both)["my email"]


# ---- hints


def test_triggers_go_in_the_prompt_and_the_text_never_does():
    prompt = glossary_prompt({"tariq": "Tareq"}, ["Zendesk"], trigger_hints(SAVED))
    assert prompt == "Glossary: Tareq, my email, Review Prompt, Zendesk."
    assert "example.com" not in prompt


def test_no_snippets_leaves_the_prompt_as_it_was():
    assert glossary_prompt({"tariq": "Tareq"}, ["Zendesk"], trigger_hints({})) == \
        glossary_prompt({"tariq": "Tareq"}, ["Zendesk"])
    assert glossary_prompt({}, [], trigger_hints(None)) is None


def test_hints_are_capped_so_the_dictionary_keeps_room():
    many = {f"phrase number {i}": "x" for i in range(MAX_TRIGGER_HINTS + 5)}
    hints = trigger_hints(many)
    assert len(hints) == MAX_TRIGGER_HINTS and hints[-1] == f"phrase number {MAX_TRIGGER_HINTS + 4}"
    assert trigger_hints({"long " * 20: "x"}) == []
    dictionary = {f"k{i}": f"Term{i}" for i in range(GLOSSARY_MAX_TERMS)}
    prompt = glossary_prompt(dictionary, (), hints)
    assert prompt.count(",") + 1 == GLOSSARY_MAX_TERMS and "Term39" in prompt and hints[0] in prompt


def test_triggers_outlast_the_dictionary_when_the_prompt_is_too_long():
    dictionary = {f"k{i}": "word" * 12 + str(i) for i in range(30)}
    prompt = glossary_prompt(dictionary, (), ["my email"])
    assert prompt.endswith("my email.") and len(prompt) <= GLOSSARY_MAX_CHARS
