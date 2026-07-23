import pytest

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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("email alice@example.com", "Email alice@example.com"),
        (
            "visit https://www.example.com/a.b?x=3.14",
            "Visit https://www.example.com/a.b?x=3.14",
        ),
        ("open www.example.com/docs/v1.2", "Open www.example.com/docs/v1.2"),
        ("version v1.2.3", "Version v1.2.3"),
        ("value 3.14 and 1,000.50", "Value 3.14 and 1,000.50"),
        ("server 192.168.1.1 at 10:30", "Server 192.168.1.1 at 10:30"),
        ("e.g. i.e. Dr. Smith U.S.A.", "e.g. i.e. Dr. Smith U.S.A."),
    ],
)
def test_preserves_structured_tokens(raw, expected):
    assert clean(raw) == expected


def test_filler_words_inside_structured_tokens_are_preserved():
    raw = "um, contact um@example.com at https://x.com/um/path?q=uh"
    assert clean(raw) == "Contact um@example.com at https://x.com/um/path?q=uh"


def test_dictionary_does_not_rewrite_structured_tokens():
    raw = "see https://trade.com and a@trade.com, then ask trade"
    assert clean(raw, {"trade": "TradeMate"}) == (
        "See https://trade.com and a@trade.com, then ask TradeMate"
    )


@pytest.mark.parametrize("raw", ["um? I agree", "uh! I agree"])
def test_removed_filler_does_not_leave_question_or_exclamation_mark(raw):
    assert clean(raw) == "I agree"


def test_preserves_multiple_structured_tokens():
    raw = "send a@b.com to https://x.com/v1.2 at 10:30"
    assert clean(raw) == "Send a@b.com to https://x.com/v1.2 at 10:30"


def test_preserves_a_structured_token_at_the_start():
    assert clean("a@b.com is ready") == "a@b.com is ready"


def test_spaces_punctuation_adjacent_to_structured_tokens():
    assert clean("contact a@b.com,now") == "Contact a@b.com, now"


def test_url_does_not_swallow_adjacent_comma_and_prose():
    assert clean("visit https://example.com,now") == "Visit https://example.com, now"


def test_url_does_not_swallow_terminal_exclamation_and_prose():
    assert clean("visit https://example.com!now") == "Visit https://example.com! now"


def test_non_url_www_substring_is_not_protected():
    assert clean("say notwww.example.com now") == "Say notwww. example. com now"


def test_time_preserves_final_period_in_meridiem():
    assert clean("wait 10:30 p.m.,now") == "Wait 10:30 p.m., now"


def test_literal_private_use_character_does_not_collide_with_placeholder():
    assert clean("\uE000 https://example.com") == "\uE000 https://example.com"
