"""speech_gate.analyze on synthetic 16 kHz PCM: what counts as speech, what
is rejected, and which window is kept for whisper."""
import array
import math
import random
import wave

import pytest

from voisr import speech_gate
from voisr.speech_gate import (
    SpeechSpan, analyze, block_dbfs, gate_wav, trim_wav)

R = 16000
BLOCK = 640  # 40 ms at 16 kHz


def noise(seconds, amp, seed=1):
    rng = random.Random(seed)
    return [rng.randint(-amp, amp) for _ in range(int(R * seconds))]


def tone(seconds, amp, hz=220):
    return [int(amp * math.sin(2 * math.pi * hz * i / R))
            for i in range(int(R * seconds))]


def pcm(*parts):
    return array.array("h", [s for part in parts for s in part])


def test_thresholds_match_the_spec():
    assert speech_gate.BLOCK_MS == 40
    assert speech_gate.PAD_BLOCKS == 2
    assert speech_gate.MIN_SPEECH_MS == 200
    assert speech_gate.SPEECH_MARGIN_DB == 3.0
    assert speech_gate.MIN_RUN_MS == 360
    assert speech_gate.GATE_SUSTAINED_MS == 160
    assert speech_gate.ENOUGH_SPEECH_MS == 1500


def test_block_dbfs_full_scale_and_silence():
    assert abs(block_dbfs(array.array("h", [32767, -32767] * 320))) < 0.01
    assert block_dbfs(array.array("h", [0] * 640)) == -math.inf
    assert block_dbfs(array.array("h")) == -math.inf


def test_digital_zeros_are_all_zeros():
    assert analyze(pcm([0] * R)) == SpeechSpan("all_zeros", 0, 0, 0)


def test_no_samples_is_all_zeros():
    assert analyze(pcm()).verdict == "all_zeros"


def test_a_quiet_room_alone_is_not_speech():
    assert analyze(pcm(noise(1, 10))).verdict == "no_speech"


def test_steady_noise_near_the_floor_is_not_speech():
    # uniform ±60 is about -59.5 dBFS RMS: steady, so nothing rises 3 dB above it
    assert analyze(pcm(noise(1, 60))).verdict == "no_speech"


def test_a_tone_below_minus_60_dbfs_is_not_speech():
    # room is about -92 dBFS, tone about -67 dBFS: 3 dB above the room floor,
    # but under the -60 dBFS absolute minimum, so it must be rejected
    span = analyze(pcm(noise(0.5, 1), tone(0.5, 20), noise(0.5, 1, seed=2)))
    assert span.verdict == "no_speech"


def test_speech_in_a_quiet_room_is_kept_with_two_blocks_of_padding():
    span = analyze(pcm(noise(0.5, 10), tone(0.5, 3000), noise(0.5, 10, seed=2)))
    assert span.verdict == "speech"
    assert 480 <= span.speech_ms <= 560
    # windows are block-aligned: the speech starts inside block 12 (8000 // 640)
    assert span.start == (int(0.5 * R) // BLOCK - 2) * BLOCK
    assert span.end == int(1.0 * R) + 2 * BLOCK


def test_a_burst_shorter_than_200_ms_is_not_speech():
    span = analyze(pcm(noise(0.5, 10), tone(0.08, 3000), noise(0.5, 10, seed=2)))
    assert span.verdict == "no_speech"
    assert span.speech_ms < 200


def test_leading_warmup_zeros_are_dropped():
    span = analyze(pcm([0] * 8000, noise(0.3, 10), tone(0.5, 3000),
                       noise(0.3, 10, seed=4)))
    assert span.verdict == "speech"
    # blocks are counted from the first non-zero sample, not from 0
    assert span.start == 8000 + (int(0.3 * R) // BLOCK - 2) * BLOCK


def test_a_pause_between_words_is_kept():
    span = analyze(pcm(noise(0.3, 10), tone(0.4, 3000), noise(1.0, 10, seed=5),
                       tone(0.4, 3000), noise(0.3, 10, seed=6)))
    assert span.verdict == "speech"
    assert span.start < int(0.3 * R) and span.end > int(2.1 * R)
    assert span.speech_ms <= 880  # the 1 s pause is kept but not counted


def test_whispered_speech_in_a_quiet_room_counts():
    # amplitude 150 is about -50 dBFS RMS
    span = analyze(pcm(noise(0.5, 10), tone(0.5, 150), noise(0.5, 10, seed=7)))
    assert span.verdict == "speech"


def test_a_loud_room_never_hides_speech():
    # room is about -45 dBFS and the tone about -44 dBFS. Without the -50 dBFS
    # floor cap the floor would be -45, the threshold -42, and the tone would
    # be rejected. With the cap the threshold is -47 and the tone is kept.
    span = analyze(pcm(noise(1, 320), tone(0.5, 300), noise(1, 320, seed=3)))
    assert span.verdict == "speech"


def test_steady_loud_sound_is_accepted_and_kept_whole():
    # The floor cap means a loud steady room reads as speech. Dropping a real
    # dictation is worse than sending whisper a noisy clip; cleanup already
    # strips [BLANK_AUDIO]-style output.
    span = analyze(pcm(noise(1, 2000)))
    assert span.verdict == "speech"
    assert (span.start, span.end) == (0, R)


# speech, longest run and sustained ms from the owner's own "Voisr audio:" rows,
# Right Option on a MacBook Air, 2026-11-04. Real keys and a real voice, but only
# 17 clips, one room and normal loudness: a soft voice is unmeasured.
REAL_SILENT = [
    (200, 80, 40), (320, 80, 0), (800, 320, 0),  # plain holds
    (160, 40, 0), (680, 120, 0),
    (720, 240, 80), (560, 240, 120), (520, 240, 120),  # tap-then-holds
    (320, 200, 120), (600, 200, 80), (840, 320, 120),
]
REAL_SPEECH = [
    (480, 360, 360), (680, 560, 320), (960, 880, 200), (680, 600, 200),  # yes no okay stop
    (720, 440, 160),  # it
    (4640, 2080, 560),  # a sentence
]


@pytest.mark.parametrize("reading", REAL_SILENT)
def test_real_silent_presses_are_not_speech(reading):
    assert not speech_gate.heard_speech(*reading)


def test_a_silent_hands_free_start_and_stop_is_not_speech():
    # an estimate, not a row: six key events at the rate four reached in real rows
    assert not speech_gate.heard_speech(1260, 320, 120)


@pytest.mark.parametrize("reading", REAL_SPEECH)
def test_real_short_words_and_a_sentence_are_speech(reading):
    assert speech_gate.heard_speech(*reading)


@pytest.mark.parametrize("reading", [(1500, 200, 0), (240, 40, 160), (360, 360, 0)])
def test_any_one_kind_of_evidence_is_enough(reading):
    assert speech_gate.heard_speech(*reading)


def blocks_at(levels):
    """One 40 ms block of a tone at each level, in dBFS (a tone's RMS is its peak
    less 3 dB). Made up, to give analyze the shape of a real row."""
    out = []
    for level in levels:
        amp = 32768 * 10 ** ((level + 3.01) / 20)
        out.extend(int(amp * math.sin(2 * math.pi * 400 * i / R)) for i in range(BLOCK))
    return out


ROOM = [-62.0] * 8
THUMPS = [-30.0, -40.0, -50.0, -55.0, -58.0, -59.0]  # falls away fast


def test_a_merged_thump_like_a_real_tap_then_hold_is_rejected():
    # the shape of a real row: a run of 240 ms, more than the old 200 ms total
    span = analyze(pcm(blocks_at(ROOM + THUMPS + ROOM * 3 + THUMPS[:2] + ROOM)))
    assert span.verdict == "no_speech"
    assert span.longest_run_ms == 240 and span.speech_ms == 320
    assert span.sustained_ms < speech_gate.GATE_SUSTAINED_MS


def test_a_short_word_passes_on_a_steady_stretch_alone():
    # no longer than a real thump's run, but its level holds
    span = analyze(pcm(blocks_at(ROOM + [-30.0] * 5 + ROOM)))
    assert span.verdict == "speech"
    assert span.longest_run_ms == 200 and span.sustained_ms == 200


def test_a_word_with_an_uneven_level_passes_on_its_run():
    uneven = [-20.0, -32.0, -44.0, -32.0, -20.0, -32.0, -44.0, -32.0, -20.0, -32.0]
    span = analyze(pcm(blocks_at(ROOM + uneven + ROOM)))
    assert span.verdict == "speech"
    assert span.longest_run_ms == 400 and span.sustained_ms < speech_gate.GATE_SUSTAINED_MS


def test_long_choppy_speech_passes_on_its_total():
    choppy = ([-30.0, -48.0] + [-62.0] * 3) * 19
    span = analyze(pcm(blocks_at(ROOM + choppy + ROOM)))
    assert span.verdict == "speech"
    assert span.speech_ms >= 1500 and span.longest_run_ms < speech_gate.MIN_RUN_MS


def test_a_rejected_clip_still_reports_its_sustained_run():
    span = analyze(pcm(blocks_at(ROOM + [-30.0] * 4 + ROOM)))
    assert span.verdict == "no_speech"
    assert span.sustained_ms == 160 and span.speech_ms == 160


def test_longest_run_counts_only_unbroken_blocks():
    gap = noise(0.2, 10, seed=2)
    span = analyze(pcm(noise(0.5, 10), tone(0.12, 3000), gap, tone(0.2, 3000), noise(0.5, 10, seed=3)))
    assert 200 <= span.longest_run_ms <= 280
    assert span.speech_ms > span.longest_run_ms


def test_the_reading_says_what_it_was_judged_against():
    span = analyze(pcm(noise(1, 10)))  # uniform +-10 is about -75 dBFS
    assert span.verdict == "no_speech"
    assert -76 < span.floor_dbfs < -74
    assert span.threshold_dbfs == speech_gate.ABS_MIN_DBFS
    assert span.longest_run_ms == 0


def test_describe_gives_every_measurement():
    span = SpeechSpan("speech", 240, 0, 16000, 80, 120, -62.14, -59.14)
    assert speech_gate.describe(span, 2.4) == (
        "clip 2.40 s, gate2, speech, speech 240 ms, longest run 120 ms, sustained 80 ms, "
        "floor -62.1 dBFS, threshold -59.1 dBFS")


def test_describe_copes_with_no_reading_and_all_zeros():
    assert speech_gate.describe(None, 1.0) == "clip 1.00 s, gate skipped: unreadable"
    assert "floor -inf dBFS" in speech_gate.describe(analyze(pcm([0] * R)), 1.0)


def write_wav(path, samples, width=2, channels=1, rate=R):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        if width == 2:
            w.writeframes(array.array("h", samples).tobytes())
        else:
            w.writeframes(bytes((s // 256 + 128) & 0xFF for s in samples))


def read_frames(path):
    with wave.open(str(path), "rb") as w:
        data = array.array("h")
        data.frombytes(w.readframes(w.getnframes()))
        return w.getparams(), list(data)


def test_gate_wav_matches_analyze(tmp_path):
    samples = noise(0.5, 10) + tone(0.5, 3000) + noise(0.5, 10, seed=2)
    write_wav(tmp_path / "a.wav", samples)
    assert gate_wav(tmp_path / "a.wav") == analyze(pcm(samples))


def test_gate_wav_skips_what_it_cannot_read(tmp_path):
    write_wav(tmp_path / "8bit.wav", tone(1, 3000), width=1)
    write_wav(tmp_path / "stereo.wav", tone(1, 3000) * 2, channels=2)
    (tmp_path / "junk.wav").write_bytes(b"\0" * 2000)
    for name in ("8bit.wav", "stereo.wav", "junk.wav", "missing.wav"):
        assert gate_wav(tmp_path / name) is None, name


def test_trim_wav_keeps_exactly_the_window(tmp_path):
    samples = list(range(1, 3201))
    write_wav(tmp_path / "a.wav", samples)
    assert trim_wav(tmp_path / "a.wav", 640, 1920) is True
    params, frames = read_frames(tmp_path / "a.wav")
    assert frames == samples[640:1920]
    assert (params.nchannels, params.sampwidth, params.framerate) == (1, 2, R)


def test_trim_wav_leaves_a_whole_window_alone(tmp_path):
    write_wav(tmp_path / "a.wav", list(range(1, 3201)))
    before = (tmp_path / "a.wav").read_bytes()
    assert trim_wav(tmp_path / "a.wav", 0, 3200) is False
    assert (tmp_path / "a.wav").read_bytes() == before


def test_gate_wav_skips_an_implausible_sample_rate(tmp_path):
    # `wave` validates nothing about the rate on write, so patch the header
    # field (bytes 24-27, little-endian uint32) the way a damaged file would.
    for rate in (0, 10):
        path = tmp_path / f"rate{rate}.wav"
        write_wav(path, tone(1, 3000))
        raw = bytearray(path.read_bytes())
        raw[24:28] = rate.to_bytes(4, "little")
        path.write_bytes(bytes(raw))
        assert gate_wav(path) is None, rate
