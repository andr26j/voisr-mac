"""Is there speech in this dictation, and where does it start and end?

Measured in 40 ms blocks of 16-bit mono PCM (spec: capture & hotkey
robustness, item 5). Plain Python, no numpy, like
stream_recorder.level_from_int16: a 10-minute clip takes about 0.5 s, on the
queue worker, never on the tap thread."""
import array
from collections import deque
import math
import operator
import os
import wave
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
# A sustained run: speech holds its level, a thump falls away fast. Judged within
# the run itself, so a thump elsewhere in the clip can't change it.
SUSTAINED_RANGE_DB = 12.0        # loudest to quietest block in the run
SUSTAINED_ABOVE_ROOM_DB = 6.0    # all of it this far above the room, even a loud one
_FULL_SCALE_SQ = 32768.0 * 32768.0


@dataclass(frozen=True)
class SpeechSpan:
    """verdict: "speech", "no_speech" or "all_zeros" (the device sent exact
    zeros only). start/end: the sample window to keep, end exclusive.
    sustained_ms: the longest unbroken run at a steady level. No part of the
    verdict: speech_ms totals scattered blocks, so key thumps can add up to it,
    and only what is riskier than plain dictation asks for this.
    longest_run_ms: the longest unbroken run at or above the threshold.
    floor_dbfs, threshold_dbfs: what the clip was judged against, -inf when it
    was all zeros."""
    verdict: str
    speech_ms: int
    start: int
    end: int
    sustained_ms: int = 0
    longest_run_ms: int = 0
    floor_dbfs: float = -math.inf
    threshold_dbfs: float = -math.inf


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


def _sustained_ms(levels, least: float) -> int:
    """The longest unbroken run of blocks at `least` or above whose loudest and
    quietest are within SUSTAINED_RANGE_DB."""
    longest = start = 0
    loud, quiet = deque(), deque()  # indexes: each run's loudest and quietest first
    for at, level in enumerate(levels):
        if level < least:
            start = at + 1
            loud.clear()
            quiet.clear()
            continue
        while loud and levels[loud[-1]] <= level:
            loud.pop()
        loud.append(at)
        while quiet and levels[quiet[-1]] >= level:
            quiet.pop()
        quiet.append(at)
        while levels[loud[0]] - levels[quiet[0]] > SUSTAINED_RANGE_DB:
            start += 1
            if loud[0] < start:
                loud.popleft()
            if quiet[0] < start:
                quiet.popleft()
        longest = max(longest, at - start + 1)
    return longest * BLOCK_MS


def _longest_run_ms(levels, least: float) -> int:
    longest = run = 0
    for level in levels:
        run = run + 1 if level >= least else 0
        longest = max(longest, run)
    return longest * BLOCK_MS


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
    room = heard[int(len(heard) * FLOOR_PERCENTILE)]
    floor = min(room, FLOOR_CAP_DBFS)
    threshold = max(floor + SPEECH_MARGIN_DB, ABS_MIN_DBFS)
    speech = [i for i, level in enumerate(levels) if level >= threshold]
    speech_ms = len(speech) * BLOCK_MS
    measured = {"longest_run_ms": _longest_run_ms(levels, threshold),
                "floor_dbfs": floor, "threshold_dbfs": threshold}
    if speech_ms < MIN_SPEECH_MS:
        return SpeechSpan("no_speech", speech_ms, first, count, **measured)
    low = max(0, speech[0] - PAD_BLOCKS)
    high = min(len(levels), speech[-1] + PAD_BLOCKS + 1)
    return SpeechSpan("speech", speech_ms, first + low * block,
                      min(count, first + high * block),
                      _sustained_ms(levels, max(threshold, room + SUSTAINED_ABOVE_ROOM_DB)),
                      **measured)


def describe(span: SpeechSpan | None, seconds: float) -> str:
    """The gate's reading for the log: measurements only, never what was said."""
    if span is None:
        return f"clip {seconds:.2f} s, gate skipped: unreadable"
    return (f"clip {seconds:.2f} s, {span.verdict}, speech {span.speech_ms} ms, "
            f"longest run {span.longest_run_ms} ms, sustained {span.sustained_ms} ms, "
            f"floor {span.floor_dbfs:.1f} dBFS, threshold {span.threshold_dbfs:.1f} dBFS")


def gate_wav(path) -> SpeechSpan | None:
    """analyze() for a WAV file. None when it isn't 16-bit mono PCM that
    can be read: the gate then skips itself and never fails a dictation."""
    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1:
                return None
            rate = w.getframerate()
            if rate < 1000:  # a damaged header: the blocks would be empty
                return None
            data = w.readframes(w.getnframes())
    except Exception:
        return None
    samples = array.array("h")
    samples.frombytes(data[:len(data) // 2 * 2])
    return analyze(samples, rate)


def trim_wav(path, start: int, end: int) -> bool:
    """Keep frames [start, end) of a WAV, in place. False (file untouched)
    when the window is the whole file."""
    with wave.open(str(path), "rb") as w:
        params = w.getparams()
        frames = w.getnframes()
        start, end = max(0, start), min(frames, end)
        if start == 0 and end == frames:
            return False
        w.setpos(start)
        data = w.readframes(end - start)
    tmp = f"{path}.trim"
    with wave.open(tmp, "wb") as out:
        out.setparams(params)
        out.writeframes(data)
    os.replace(tmp, str(path))
    return True
