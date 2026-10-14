"""Is there speech in this dictation, and where does it start and end?

Measured in 40 ms blocks of 16-bit mono PCM (spec: capture & hotkey
robustness, item 5). Plain Python, no numpy, like
stream_recorder.level_from_int16: a 10-minute clip takes about 0.5 s, on the
queue worker, never on the tap thread."""
import math
import operator
from dataclasses import dataclass

SAMPLERATE = 16000
BLOCK_MS = 40
PAD_BLOCKS = 2           # kept on each side of the speech, so whisper hears the onset
MIN_SPEECH_MS = 200      # less than this is a tap or a cough, not a dictation
SPEECH_MARGIN_DB = 3.0   # a speech block sits this far above the room's floor
FLOOR_PERCENTILE = 0.10  # the room's floor: the quietest tenth of the clip
# The floor adapts to the room but never climbs above this, so a loud room
# can't make normal speech (-35..-20 dBFS) read as silence.
FLOOR_CAP_DBFS = -50.0
ABS_MIN_DBFS = -60.0     # nothing quieter is speech, however silent the room
_FULL_SCALE_SQ = 32768.0 * 32768.0


@dataclass(frozen=True)
class SpeechSpan:
    """verdict: "speech", "no_speech" or "all_zeros" (the device sent exact
    zeros only). start/end: the sample window to keep, end exclusive."""
    verdict: str
    speech_ms: int
    start: int
    end: int


def block_dbfs(samples) -> float:
    """RMS level of one block in dBFS; -inf for an empty or all-zero block."""
    count = len(samples)
    if not count:
        return -math.inf
    mean_square = sum(map(operator.mul, samples, samples)) / count
    if mean_square <= 0:
        return -math.inf
    return 10 * math.log10(mean_square / _FULL_SCALE_SQ)


def _first_nonzero(samples) -> int:
    for index, sample in enumerate(samples):
        if sample:
            return index
    return -1


def analyze(samples, samplerate: int = SAMPLERATE) -> SpeechSpan:
    """Classify a clip and find its padded speech window.

    Exact zeros before the first non-zero sample are device warm-up and are
    dropped. Interior pauses are kept: only leading and trailing non-speech
    beyond the padding falls outside the window."""
    samples = memoryview(samples)
    count = len(samples)
    first = _first_nonzero(samples)
    if first < 0:
        return SpeechSpan("all_zeros", 0, 0, 0)
    block = samplerate * BLOCK_MS // 1000
    levels = [block_dbfs(samples[at:at + block])
              for at in range(first, count, block)]
    heard = sorted(level for level in levels if level > -math.inf)
    floor = min(heard[int(len(heard) * FLOOR_PERCENTILE)], FLOOR_CAP_DBFS)
    threshold = max(floor + SPEECH_MARGIN_DB, ABS_MIN_DBFS)
    speech = [i for i, level in enumerate(levels) if level >= threshold]
    speech_ms = len(speech) * BLOCK_MS
    if speech_ms < MIN_SPEECH_MS:
        return SpeechSpan("no_speech", speech_ms, first, count)
    low = max(0, speech[0] - PAD_BLOCKS)
    high = min(len(levels), speech[-1] + PAD_BLOCKS + 1)
    return SpeechSpan("speech", speech_ms, first + low * block,
                      min(count, first + high * block))
