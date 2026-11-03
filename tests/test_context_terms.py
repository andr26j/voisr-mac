"""Component B: which on-screen tokens may bias whisper, and how."""
import pytest

from voisr.ax_context import FocusContext
from voisr.common_words import is_common_word, is_everyday_lowercase_word
from voisr.context_terms import context_terms, prompt_echoed, terms_for_clip
from voisr.ui import GLOSSARY_MAX_CHARS, GLOSSARY_MAX_TERMS, glossary_prompt


@pytest.mark.parametrize("text, expected", [
    ("we met Priya at the office", ["Priya"]),
    ("Priya met us", []),                                   # sentence-initial
    ("Done. Priya met us", []),
    ("Done: Priya met us", []),
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
    ("all done. ", "Priya today", []),          # a new sentence
    ("", "Priya today", []),                    # start of the field
])
def test_text_after_the_caret_carries_on_the_sentence(before, after, expected):
    assert context_terms(FocusContext(before_text=before, after_text=after)) == expected


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
