"""speech_gate.analyze on synthetic 16 kHz PCM: what counts as speech, what
is rejected, and which window is kept for whisper."""
import array
import math
import random

from voisr import speech_gate
from voisr.speech_gate import SpeechSpan, analyze, block_dbfs

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


def test_noise_just_under_the_absolute_floor_is_not_speech():
    # uniform ±60 is about -59.5 dBFS RMS: steady, so nothing rises 3 dB above it
    assert analyze(pcm(noise(1, 60))).verdict == "no_speech"


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
    span = analyze(pcm(noise(1, 2000), tone(0.5, 8000), noise(1, 2000, seed=3)))
    assert span.verdict == "speech"


def test_steady_loud_sound_is_accepted_and_kept_whole():
    # The floor cap means a loud steady room reads as speech. Dropping a real
    # dictation is worse than sending whisper a noisy clip; cleanup already
    # strips [BLANK_AUDIO]-style output.
    span = analyze(pcm(noise(1, 2000)))
    assert span.verdict == "speech"
    assert (span.start, span.end) == (0, R)
