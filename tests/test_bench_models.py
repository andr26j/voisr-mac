import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "bench_models", Path(__file__).resolve().parent.parent / "scripts" / "bench_models.py")
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)


@pytest.mark.parametrize("ref,hyp,errors,words", [
    ("the cat sat", "the cat sat", 0, 3),
    ("the cat sat", "the dog sat", 1, 3),          # substitution
    ("the cat sat", "the sat", 1, 3),              # deletion
    ("the cat sat", "the big cat sat", 1, 3),      # insertion
    ("the cat sat", "", 3, 3),                     # everything missed
    ("", "extra words", 2, 0),                     # nothing to say, said two
    ("Hello, World!", "hello world", 0, 2),        # case and punctuation ignored
    ("it's fine", "it’s fine", 0, 2),         # curly apostrophe
    ("pay $1,250", "pay 1 250", 0, 3),             # punctuation splits, not scored
])
def test_word_errors(ref, hyp, errors, words):
    assert bench.word_errors(ref, hyp) == (errors, words)


def test_corpus_wer_weights_by_reference_words():
    # 1 error in 2 words + 0 errors in 8 words = 1/10, not the mean of 50% and 0%
    pairs = [("a b", "a c"), ("one two three four five six seven eight",
                              "one two three four five six seven eight")]
    assert bench.corpus_wer(pairs) == pytest.approx(0.1)


def test_corpus_wer_can_exceed_one_and_handles_empty():
    assert bench.corpus_wer([("a", "x y z")]) == pytest.approx(3.0)
    assert bench.corpus_wer([]) is None


def test_percentile():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert bench.percentile(values, 50) == 3.0
    assert bench.percentile(values, 95) == pytest.approx(4.8)
    assert bench.percentile([7.0], 95) == 7.0
    assert bench.percentile([], 95) is None


def test_find_clips_pairs_wav_with_reference(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"x")
    (tmp_path / "a.txt").write_text(" hello there \n")
    (tmp_path / "b.wav").write_bytes(b"x")                 # no reference
    (tmp_path / "c.wav").write_bytes(b"x")
    (tmp_path / "c.txt").write_text("  \n")                # empty reference
    assert bench.find_clips(tmp_path) == [(tmp_path / "a.wav", "hello there")]


def test_installed_models_ignores_other_files(tmp_path):
    for name in ("ggml-small.en.bin", "ggml-base.en.bin", "ggml-x.bin.part", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    assert bench.installed_models(tmp_path) == ["base.en", "small.en"]


def test_summarize():
    rows = [{"clip": "a", "seconds": 1.0, "reference": "a b", "hypothesis": "a b"},
            {"clip": "b", "seconds": 3.0, "reference": "c d", "hypothesis": "c x"}]
    s = bench.summarize("base.en", rows, 300.0)
    assert (s["clips"], s["wer"], s["median_s"], s["peak_mb"]) == (2, 0.25, 2.0, 300.0)


def _wav(path, samples):
    import array, wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(array.array("h", samples).tobytes())


def test_prepare_clip_trims_a_copy_and_never_the_corpus_file(tmp_path):
    import math
    corpus, work = tmp_path / "c", tmp_path / "w"
    corpus.mkdir(); work.mkdir()
    tone = [int(8000 * math.sin(i / 5)) for i in range(16000)]
    wav = corpus / "a.wav"
    _wav(wav, [0] * 32000 + tone + [0] * 32000)
    before = wav.read_bytes()
    out = bench.prepare_clip(wav, work)
    assert out is not None and out.parent == work
    assert wav.read_bytes() == before
    assert out.stat().st_size < len(before)


def test_prepare_clip_drops_what_the_gate_rejects(tmp_path):
    wav = tmp_path / "silent.wav"
    _wav(wav, [0] * 16000)
    work = tmp_path / "w"; work.mkdir()
    assert bench.prepare_clip(wav, work) is None


def test_summarize_ignores_gated_rows_for_latency():
    rows = [{"clip": "a", "seconds": 2.0, "reference": "a", "hypothesis": "a"},
            {"clip": "b", "seconds": None, "gated": True, "reference": "b", "hypothesis": ""}]
    s = bench.summarize("m", rows, None)
    assert s["median_s"] == 2.0 and s["wer"] == 0.5
