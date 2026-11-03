"""Component B: which on-screen tokens may bias whisper, and how."""
from pathlib import Path

import pytest

from voisr.ax_context import FocusContext
from voisr.common_words import is_common_word, is_everyday_lowercase_word
from voisr.context_terms import (cli_safe_terms, context_terms, prompt_echoed,
                                 terms_for_clip)
from voisr.ui import GLOSSARY_MAX_CHARS, GLOSSARY_MAX_TERMS, glossary_prompt


@pytest.fixture(autouse=True)
def system_dictionary(monkeypatch):
    """A small stand-in, so results don't depend on this Mac's word list."""
    words = {"meanwhile", "hopefully"}
    monkeypatch.setattr("voisr.context_terms.everyday_word_check",
                        lambda: lambda word: word.casefold() in words)


@pytest.mark.parametrize("text, expected", [
    ("we met Priya at the office", ["Priya"]),
    ("Priya met us", ["Priya"]),                            # opens a sentence
    ("Done. Priya met us", ["Priya"]),
    ("Done: Priya met us", ["Priya"]),
    ("Priya", ["Priya"]),                                   # alone in a field
    ("Priya.\nTareq.\n", ["Priya", "Tareq"]),               # a line each
    ("Meanwhile we left. Hopefully soon.", []),             # ordinary words
    ("ask Priya's team", ["Priya"]),                        # possessive
    ("see Monday and the Internet", []),                    # stoplist
    ("run getUserName now", ["getUserName"]),               # camelCase
    ("GitHub rocks", ["GitHub"]),                           # Pascal, even first
    ("use max_retry_count here", ["max_retry_count"]),      # snake_case
    ("ping @andre and #eng-team", ["@andre", "#eng-team"]),
    ("the M1 and gpt4 chips", ["M1", "gpt4"]),              # mixed
    ("mail me at someone@example.com", []),                 # no emails
    ("at https://example.com/Path today", []),              # no URLs
    ("code 123456 and 2024", []),                           # only digits
    ("key sk4a9b8c7d6e5f4a3b2c1d0e", []),                   # shaped like a secret
    ("token 12345a", []),                                   # digit-heavy
    ("the, quick (brown) fox", []),
])
def test_what_counts_as_a_term(text, expected):
    assert context_terms(FocusContext(after_text=text, before_text="")) == expected


def test_nearest_the_caret_comes_first_and_sources_keep_their_order():
    focus = FocusContext(
        selected_text="pick Selwyn now",
        before_text="far Alpha near Beta here",
        after_text="then Gamma later Delta",
        nearby_texts=("label Nearby", ),
        window_title="in Titled window")
    assert context_terms(focus) == ["Selwyn", "Beta", "Gamma", "Alpha", "Delta",
                                    "Nearby", "Titled"]


@pytest.mark.parametrize("before, after, expected", [
    ("say hi to ", "Priya today", ["Priya"]),   # mid-sentence at the caret
    ("all done. ", "Priya today", ["Priya"]),   # a new sentence
    ("", "Priya today", ["Priya"]),             # start of the field
])
def test_text_after_the_caret_carries_on_the_sentence(before, after, expected):
    assert context_terms(FocusContext(before_text=before, after_text=after)) == expected


def test_openers_come_after_every_other_term_and_only_fill_what_is_left():
    focus = FocusContext(before_text="Tareq said hi to Alpha. ", after_text="Priya met Beta")
    assert context_terms(focus) == ["Alpha", "Beta", "Tareq", "Priya"]
    assert context_terms(focus, limit=2) == ["Alpha", "Beta"]
    assert context_terms(focus, limit=3) == ["Alpha", "Beta", "Tareq"]


def test_the_dictionary_is_read_once_and_only_for_an_opener(monkeypatch):
    calls = []

    def check():
        calls.append(1)
        return lambda word: False

    monkeypatch.setattr("voisr.context_terms.everyday_word_check", check)
    assert context_terms(FocusContext(after_text="we met Priya")) == ["Priya"]
    assert calls == []
    assert context_terms(FocusContext(after_text="Priya. Tareq. Selwyn.")) == [
        "Priya", "Tareq", "Selwyn"]
    assert calls == [1]


def test_checks_are_capped_on_a_long_document():
    from voisr.context_terms import MAX_OPENERS_CHECKED

    many = " ".join(f"Name{chr(97 + i // 26)}{chr(97 + i % 26)}x." for i in range(60))
    assert len(context_terms(FocusContext(after_text=many), limit=50)) == MAX_OPENERS_CHECKED


def test_a_dictionary_that_cannot_be_read_keeps_openers_out(monkeypatch, tmp_path):
    from voisr.common_words import everyday_word_check

    monkeypatch.setattr("voisr.context_terms.everyday_word_check",
                        lambda: everyday_word_check(tmp_path / "missing"))
    assert context_terms(FocusContext(after_text="Priya met us")) == []


@pytest.mark.skipif(not Path("/usr/share/dict/words").is_file(),
                    reason="needs the system dictionary")
def test_the_real_system_dictionary_tells_a_name_from_a_word():
    from voisr.common_words import everyday_word_check

    check = everyday_word_check()
    assert check("Meanwhile") and check("hopefully")
    assert not check("Tareq") and not check("Priya")


def test_sentence_openers_stay_off_the_command_line():
    terms = context_terms(FocusContext(after_text="Qwertzuiop. we met Priya"))
    assert terms == ["Priya", "Qwertzuiop"]
    assert cli_safe_terms(terms) == ["Priya"]
    assert cli_safe_terms(terms_for_clip(terms, 1.0)) == ["Priya"]


def test_the_last_word_of_a_list_with_no_final_newline_counts(tmp_path):
    from voisr.common_words import everyday_word_check

    words = tmp_path / "words"
    words.write_bytes(b"first\nlast")
    check = everyday_word_check(words)
    assert check("first") and check("last") and not check("middle")


def test_dedupes_case_insensitively_and_limits():
    assert context_terms(FocusContext(after_text="a Zed then ZED and zEd.")) == ["Zed"]
    many = " ".join(f"x Name{chr(97 + i)}{chr(97 + i)}" for i in range(26))
    assert len(context_terms(FocusContext(after_text=many), limit=20)) == 20


@pytest.mark.parametrize("focus", [
    None,
    FocusContext(secure=True, before_text="not Priya here"),
])
def test_nothing_from_no_capture_or_a_secure_one(focus):
    assert context_terms(focus) == []


@pytest.mark.parametrize("bundle_id, keeps_mixed", [
    ("com.apple.TextEdit", True),
    ("com.tinyspeck.slackmacgap", True),
    ("com.apple.Terminal", False),
    ("com.googlecode.iterm2", False),
    ("com.google.Chrome", False),
    ("com.apple.Safari", False),
])
def test_mixed_tokens_are_dropped_from_terminals_and_browsers(bundle_id, keeps_mixed):
    focus = FocusContext(bundle_id=bundle_id, before_text="",
                         after_text="the code Ab3xK9 and M1 for Priya with getUserName")
    terms = context_terms(focus)
    assert ("Ab3xK9" in terms) is keeps_mixed
    assert ("M1" in terms) is keeps_mixed
    # legitimate terms keep working everywhere
    assert "Priya" in terms and "getUserName" in terms


def test_no_mixed_token_is_safe_for_the_command_line():
    terms = ["Priya", "gpt4", "M1", "@user1", "getUserName", "max_retry", "v2_key"]
    assert cli_safe_terms(terms) == ["Priya", "getUserName", "max_retry"]


def test_short_clip_cap():
    terms = [f"T{i}" for i in range(20)]
    assert terms_for_clip(terms, 1.9) == terms[:5]
    assert terms_for_clip(terms, 2.0) == terms


@pytest.mark.parametrize("dictionary, terms, expected", [
    (None, (), None),
    ({}, (), None),
    ({"a": "Voisr"}, (), "Glossary: Voisr."),
    ({}, ["Priya"], "Glossary: Priya."),
    ({"a": "Voisr"}, ["Priya"], "Glossary: Voisr, Priya."),
    ({"a": "Voisr"}, ["VOISR", "Priya", "priya"], "Glossary: Voisr, Priya."),
])
def test_glossary_merge(dictionary, terms, expected):
    assert glossary_prompt(dictionary, terms) == expected


def test_context_terms_only_fill_the_budget_the_dictionary_leaves():
    full = {f"k{i}": f"Word{i}" for i in range(GLOSSARY_MAX_TERMS)}
    assert "Priya" not in glossary_prompt(full, ["Priya"])
    assert glossary_prompt(full, ["Priya"]) == glossary_prompt(full)

    terms = [f"Termname{i}" for i in range(GLOSSARY_MAX_TERMS)]
    prompt = glossary_prompt({"k": "Word"}, terms)
    assert prompt.startswith("Glossary: Word, Termname0")
    assert prompt.count(",") + 1 <= GLOSSARY_MAX_TERMS

    long = [("Longterm" + "x" * 20 + str(i)) for i in range(GLOSSARY_MAX_TERMS)]
    prompt = glossary_prompt({"k": "Word"}, long)
    assert len(prompt) <= GLOSSARY_MAX_CHARS
    assert "Word" in prompt


@pytest.mark.parametrize("raw, echoed", [
    ("Glossary: Priya, Zed, Mina.", True),
    ("glossary : something", True),
    ("Priya, Zed, Mina.", True),
    ("I met Priya and Zed today.", False),
    ("Priya and Zed and Mina", False),
    ("the glossary of terms", False),
])
def test_prompt_echo(raw, echoed):
    assert prompt_echoed(raw, ["Priya", "Zed", "Mina"]) is echoed


TERMS = ["Priya", "Zedwick", "Mina", "Raman", "Kestrelway", "Quindle"]


@pytest.mark.parametrize("raw, seconds", [
    # nothing but terms from a clip too short to be anything else
    ("Priya, Mina.", 0.8),
    ("Zedwick Raman", 1.5),
    ("Priya. Priya. Priya.", 1.0),
    # mostly terms, out of order so the run rule misses them
    ("Mina and Priya, also Raman.", 1.5),
    # far more than the clip can hold, even past the short-clip cap
    ("Kestrelway Quindle Zedwick Raman and Priya Mina Kestrelway Quindle", 2.5),
])
def test_output_that_is_mostly_terms_the_clip_cant_hold_is_an_echo(raw, seconds):
    assert prompt_echoed(raw, TERMS, seconds) is True


@pytest.mark.parametrize("raw, seconds", [
    # normal dictation with one or two on-screen names
    ("Priya", 0.8),                                  # just the name
    ("Thanks Priya", 1.0),
    ("Thanks Priya Raman", 1.2),
    ("Send it to Priya.", 1.2),
    ("Send it to Priya and Mina today.", 2.5),
    ("Please ask Priya to review the Kestrelway dashboard before Friday.", 4.0),
    # names only, but said at a speaking pace over a real length of clip
    ("Priya Raman", 2.5),
    ("Mina and Priya, also Raman.", 4.0),
    ("", 1.0),
])
def test_normal_dictation_with_a_name_or_two_is_not_an_echo(raw, seconds):
    assert prompt_echoed(raw, TERMS, seconds) is False


def test_common_words_catch_inflections():
    for word in ("Tuesday", "tuesdays", "stopped", "replies", "liking", "THE"):
        assert is_common_word(word), word
    for word in ("Voisr", "Priya", "GitHub"):
        assert not is_common_word(word), word


def test_system_dictionary_checks_lowercase_spellings_only(tmp_path):
    words = tmp_path / "words"
    words.write_text("Claude\nclod\nvoice\nzebra\n")
    assert is_everyday_lowercase_word("clod", words)
    assert is_everyday_lowercase_word("Clods", words)
    assert not is_everyday_lowercase_word("claude", words)
    assert not is_everyday_lowercase_word("voisr", words)


def test_an_unreadable_system_dictionary_refuses(tmp_path):
    assert is_everyday_lowercase_word("voisr", tmp_path / "missing")
