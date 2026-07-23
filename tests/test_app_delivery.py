import voisr.app as app
from voisr.pasteboard import PasteDeliveryError


def test_cli_preservation_error_does_not_copy_over_original(monkeypatch, capsys):
    copied = []
    monkeypatch.setattr(
        app, "paste_at_cursor",
        lambda text: (_ for _ in ()).throw(PasteDeliveryError(
            "clipboard preserved", pasted=False, fallback_safe=False)),
    )
    monkeypatch.setattr(app, "set_clipboard", copied.append)

    app.deliver("dictation", paste=True)

    assert copied == []
    assert "clipboard preserved" in capsys.readouterr().out


def test_cli_restore_error_reports_pasted_without_fallback(monkeypatch, capsys):
    copied = []
    monkeypatch.setattr(
        app, "paste_at_cursor",
        lambda text: (_ for _ in ()).throw(PasteDeliveryError(
            "restore failed", pasted=True, fallback_safe=False)),
    )
    monkeypatch.setattr(app, "set_clipboard", copied.append)

    app.deliver("dictation", paste=True)

    assert copied == []
    output = capsys.readouterr().out
    assert "pasted" in output
    assert "restore failed" in output


def test_cli_safe_delivery_error_copies_text_for_manual_paste(monkeypatch, capsys):
    copied = []
    monkeypatch.setattr(
        app, "paste_at_cursor",
        lambda text: (_ for _ in ()).throw(PasteDeliveryError(
            "paste unavailable", pasted=False, fallback_safe=True)),
    )
    monkeypatch.setattr(app, "set_clipboard", copied.append)

    app.deliver("dictation", paste=True)

    assert copied == ["dictation"]
    assert capsys.readouterr().out == (
        "copied to clipboard (paste blocked or disabled): dictation\n")
