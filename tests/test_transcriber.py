from wisprlocal.transcriber import transcribe


def test_transcribes_synthesized_speech(spoken_wav):
    wav = spoken_wav("Hello world, this is a test.")
    text = transcribe(wav)
    assert "hello world" in text.lower()
    assert "test" in text.lower()
