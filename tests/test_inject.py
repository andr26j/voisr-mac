from wisprlocal.inject import get_clipboard, set_clipboard


def test_clipboard_roundtrip():
    old = get_clipboard()
    try:
        set_clipboard("wispr-local test ✓")
        assert get_clipboard() == "wispr-local test ✓"
    finally:
        set_clipboard(old)
