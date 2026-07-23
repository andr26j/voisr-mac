from time import perf_counter

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


def test_url_preserves_adjacent_comma_and_prose_as_uri_text():
    assert clean("visit https://example.com,now") == "Visit https://example.com,now"


def test_url_preserves_adjacent_exclamation_and_prose_as_uri_text():
    assert clean("visit https://example.com!now") == "Visit https://example.com!now"


def test_non_url_www_substring_is_not_protected():
    assert clean("say notwww.example.com now") == "Say notwww. example. com now"


def test_time_preserves_final_period_in_meridiem():
    assert clean("wait 10:30 p.m.,now") == "Wait 10:30 p.m., now"


def test_literal_private_use_character_does_not_collide_with_placeholder():
    assert clean("\uE000 https://example.com") == "\uE000 https://example.com"


def test_preserves_nested_redirect_query_and_fragment():
    raw = "open https://example.com/redirect?next=https://target.test/a:b,c;d?x=1#inner#outer"
    assert clean(raw) == "Open https://example.com/redirect?next=https://target.test/a:b,c;d?x=1#inner#outer"


def test_preserves_ipv4_url_with_port_path_query_and_fragment():
    raw = "open http://127.0.0.1:8000/v1.2?x=3.14#now"
    assert clean(raw) == "Open http://127.0.0.1:8000/v1.2?x=3.14#now"


def test_preserves_localhost_url_with_port():
    assert clean("open http://localhost:3000/a:b") == "Open http://localhost:3000/a:b"


def test_preserves_uri_commas_colons_and_semicolons():
    raw = "open https://example.com/a:b,c;d?x=one,two;three#now"
    assert clean(raw) == "Open https://example.com/a:b,c;d?x=one,two;three#now"


def test_unicode_embedded_www_substring_is_not_protected():
    assert clean("say éwww.example.com now") == "Say éwww. example. com now"


def test_preserves_urls_wrapped_in_parentheses_and_quotes():
    raw = 'open (https://example.com/a:b) and "https://example.com/q"'
    assert clean(raw) == 'Open (https://example.com/a:b) and "https://example.com/q"'


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("wait 10:30 a.m.)", "Wait 10:30 a.m.)"),
        ('wait 10:30 p.m."', 'Wait 10:30 p.m."'),
    ],
)
def test_time_preserves_meridiem_period_before_closing_delimiter(raw, expected):
    assert clean(raw) == expected


def test_dictionary_cannot_rewrite_inserted_placeholder_marker():
    marker = "\uE000\uE001\uE002"
    raw = "\uE000 see https://example.com"
    assert clean(raw, {marker: "changed"}) == raw


def test_dictionary_phrases_apply_around_multiple_protected_spans():
    raw = "ask trade mate at a@trade.com then visit https://trade.com trade mate"
    assert clean(raw, {"trade mate": "TradeMate"}) == (
        "Ask TradeMate at a@trade.com then visit https://trade.com TradeMate"
    )


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("https://example.com", "Say https: //example. com"),
        ("a@example.com", "Say a@example. com"),
    ],
)
def test_dictionary_generated_structured_text_keeps_base_cleanup_behavior(written, expected):
    assert clean("say token", {"token": written}) == expected


def test_dictionary_replacements_keep_cascade_order():
    assert clean("say alpha", {"alpha": "beta", "beta": "gamma"}) == "Say gamma"


def test_unmatched_closing_parenthesis_and_suffix_are_unprotected():
    assert clean("(https://example.com),now") == "(https://example.com), now"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"https://example.com",now', '"https://example.com", now'),
        ("'https://example.com'.now", "'https://example.com'. now"),
    ],
)
def test_closing_quote_and_following_text_are_unprotected(raw, expected):
    assert clean(raw) == expected


def test_url_keeps_balanced_parentheses_and_maximal_uri_punctuation():
    assert clean("open https://example.com/a(b),now") == "Open https://example.com/a(b),now"


@pytest.mark.parametrize(
    "raw",
    ['"https://example.com"now', "'https://example.com'now"],
)
def test_matching_original_quote_leaves_following_prose_unprotected(raw):
    expected = raw.replace("now", "later")
    assert clean(raw, {"now": "later"}) == expected


def test_url_with_internal_apostrophe_without_opening_wrapper_stays_exact():
    assert clean("open https://example.com/can't") == "Open https://example.com/can't"


def test_url_at_start_has_no_opening_wrapper_context():
    assert clean("https://example.com") == "https://example.com"


def test_single_quote_wrapper_closes_after_internal_apostrophe():
    raw = "'https://example.com/can't'now"
    assert clean(raw, {"t": "T", "now": "later"}) == "'https://example.com/can't'later"


def test_double_quote_wrapper_closes_after_internal_quote_data():
    raw = '"https://example.com/a"b"now'
    assert clean(raw, {"b": "B", "now": "later"}) == '"https://example.com/a"b"later'


def test_unmatched_opening_quote_keeps_the_maximal_url_candidate():
    raw = '"https://example.com/pathnow'
    assert clean(raw, {"now": "later"}) == raw


def test_unmatched_double_quote_wrapper_preserves_internal_single_quote():
    raw = '"https://example.com/path?x=\''
    assert clean(raw, {"'": "X"}) == raw


def test_unmatched_single_quote_wrapper_preserves_internal_double_quote():
    raw = "'https://example.com/path?x=\""
    assert clean(raw, {'"': "X"}) == raw


@pytest.mark.parametrize(
    ("raw", "dictionary"),
    [
        ("https://one.test)https://two.test/a.b", {"two": "TWO"}),
        ("https://one.test)a@b.com", {"b": "B"}),
    ],
)
def test_url_suffix_rescan_protects_structured_tokens_after_wrapper(raw, dictionary):
    assert clean(raw, dictionary) == raw


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("value .5 -.5 +0.5", "Value .5 -.5 +0.5"),
        ("Dr. Ada Ph.D.", "Dr. Ada Ph.D."),
        ("mail élise@exämple.世界", "Mail élise@exämple.世界"),
        ("mail user@example.xn--p1ai", "Mail user@example.xn--p1ai"),
    ],
)
def test_preserves_expanded_practical_structured_tokens(raw, expected):
    assert clean(raw) == expected


@pytest.mark.parametrize(
    ("opening", "closing"),
    [
        ("(", ")"),
        ("[", "]"),
        ("{", "}"),
        ("<", ">"),
        ('"', '"'),
        ("'", "'"),
        ("“", "”"),
        ("‘", "’"),
        ("«", "»"),
        ("‹", "›"),
    ],
)
def test_wrapped_url_leaves_following_prose_unprotected(opening, closing):
    raw = f"{opening}https://example.com{closing}now"
    assert clean(raw, {"now": "later"}) == f"{opening}https://example.com{closing}later"


def test_angle_wrapped_url_leaves_punctuation_and_prose_unprotected():
    assert clean("<https://example.com>,now", {"now": "later"}) == (
        "<https://example.com>, later"
    )


def test_time_preserves_meridiem_period_before_smart_quote_closer():
    raw = "wait 10:30 p.m.”now"
    assert clean(raw, {"now": "later"}) == "Wait 10:30 p.m.”later"


def test_many_structured_tokens_scale_near_linearly():
    raw = " ".join(["1.2"] * 2_000)
    started = perf_counter()
    assert clean(raw) == raw
    assert perf_counter() - started < 2


def test_near_miss_email_scales_linearly():
    raw = "a" * 8_000 + "@" + "b" * 8_000
    started = perf_counter()
    assert clean(raw) == "A" + raw[1:]
    assert perf_counter() - started < 0.5


def test_quote_dense_url_scales_linearly():
    url = "https://example.com/" + "'a" * 400_000
    started = perf_counter()
    assert clean(f"open {url}") == f"Open {url}"
    assert perf_counter() - started < 0.75


def test_placeholder_namespace_handles_combined_adversarial_private_use_content():
    prefix = "".join(chr(codepoint) for codepoint in range(0xE000, 0xF900)) + "\uE000" * 120_000
    raw = f"{prefix} https://example.com"
    started = perf_counter()
    assert clean(raw) == raw
    assert perf_counter() - started < 0.75


def test_dotted_prose_is_not_protected_as_an_abbreviation():
    raw = "yes.No. go.Now. hot.It.Is"
    dictionary = {"No": "NO", "Now": "NOW", "It": "IT", "Is": "IS"}
    assert clean(raw, dictionary) == "Yes. NO. go. NOW. hot. IT. IS"


def test_signed_grouped_numbers_protect_their_signs_from_dictionary_rewrites():
    raw = "values -1,000.50 +1,000.50"
    assert clean(raw, {"-": "minus", "+": "plus"}) == "Values -1,000.50 +1,000.50"


@pytest.mark.parametrize(
    ("raw", "dictionary", "expected"),
    [
        ("I.am.Done.", {"am": "AM", "Done": "DONE"}, "I. AM. DONE."),
        ("A.go.Now.", {"go": "GO", "Now": "NOW"}, "A. GO. NOW."),
        ("go.I.Now.", {"I": "eye", "Now": "NOW"}, "Go. eye. NOW."),
    ],
)
def test_mixed_length_dotted_prose_keeps_legacy_cleanup(raw, dictionary, expected):
    assert clean(raw, dictionary) == expected


@pytest.mark.parametrize(
    ("raw", "dictionary", "expected"),
    [
        ("\ud800 https://example.com", None, "\ud800 https://example.com"),
        ("say token", {"token": "\ud800"}, "Say \ud800"),
    ],
)
def test_namespace_digest_accepts_lone_surrogates(raw, dictionary, expected):
    assert clean(raw, dictionary) == expected
