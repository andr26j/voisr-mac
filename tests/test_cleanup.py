from voisr.cleanup import clean


def test_removes_filler_words():
    assert clean("Um, I think, uh, we should go.") == "I think, we should go."


def test_collapses_whitespace_and_orphan_punct():
    assert clean("hello   world ,  again") == "Hello world, again"


def test_capitalizes_first_letter():
    assert clean("hello there.") == "Hello there."


def test_applies_custom_dictionary():
    assert clean("talk to trade mate support", dictionary={"trade mate": "TradeMate"}) == "Talk to TradeMate support"


def test_dictionary_matches_whole_words_only():
    assert clean("filed under category cat", dictionary={"cat": "Katherine"}) == "Filed under category Katherine"


def test_dictionary_replacement_with_backslash_is_literal():
    assert clean("say backslash", dictionary={"backslash": r"a\b"}) == r"Say a\b"


def test_empty_input():
    assert clean("") == ""


def test_filler_only_input():
    assert clean("um, uh, hmm") == ""
