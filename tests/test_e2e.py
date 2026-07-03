from voisr.app import load_config, process_audio


def test_full_pipeline_from_audio_to_clean_text(spoken_wav):
    wav = spoken_wav("Hello world. Please email the team about the demo.")
    text = process_audio(wav, dictionary={})
    assert "hello world" in text.lower()
    assert "email the team" in text.lower()
    assert text[0].isupper()


def test_pipeline_applies_dictionary(spoken_wav):
    # A dictionary maps every ASR-heard variant of a term to its written form,
    # so it must fix the transcript no matter which variant whisper produces.
    wav = spoken_wav("Send the report to trade mate today.")
    variants = {"trade mate": "TradeMate", "trademake": "TradeMate", "trademate": "TradeMate"}
    text = process_audio(wav, dictionary=variants)
    assert "TradeMate" in text


def test_load_config_defaults(tmp_path):
    cfg = load_config(tmp_path / "missing.json")
    assert cfg["dictionary"] == {}
    assert cfg["paste"] is True
