"""Narration stage: marker chunking, knob/silence mapping, word-timing math.

Consumes approved per-segment scripts (`build/scripts/segment-<n>.txt` + the index)
and produces narration audio (`build/narration/segment-<n>.mp3`) plus cumulative
word timing (`build/narration/segment-<n>.timing.jsonl`). Real TTS (edge-tts) and
the audio codec (ffmpeg) live behind the `Synthesizer`/`Encoder` seams so the core
stays offline-testable and deterministic. Markers are structural: never spoken,
never present in output.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NamedTuple, Protocol, cast

from . import config, layout, script

ChunkKind = Literal["base", "keyword", "figure", "gold", "pause"]


@dataclass(frozen=True)
class Chunk:
    text: str
    kind: ChunkKind
    pre_silence_ms: int
    post_silence_ms: int


# docs/specs/narration.md §4: the emphasis -> prosody mapping (rate, volume).
KNOBS: dict[ChunkKind, tuple[str, str]] = {
    "base": ("0%", "0%"),
    "keyword": ("-8%", "+12%"),
    "figure": ("-5%", "+10%"),
    "gold": ("-8%", "+15%"),
}

# (pre_ms, post_ms) silence per kind (spec §4).
SILENCE_MS: dict[ChunkKind, tuple[int, int]] = {
    "base": (0, 0),
    "keyword": (120, 0),
    "figure": (0, 450),
    "gold": (0, 450),
    "pause": (300, 0),
}

_TOKEN_RE = re.compile(r"(\*\*[^*]+\*\*|##[^#]+##|~)")


def parse_line(line: str) -> list[Chunk]:
    """Split one script line into ordered chunks at marker boundaries.

    Markers are stripped and never appear in `Chunk.text`. Consecutive base runs
    merge. An exact `**gold**` marker is structural (kind `gold`, empty text).
    """
    out: list[Chunk] = []
    for part in _TOKEN_RE.split(line):
        if not part:
            continue
        if part == "~":
            out.append(Chunk("", "pause", *SILENCE_MS["pause"]))
        elif part.startswith("##") and part.endswith("##"):
            out.append(Chunk(part[2:-2], "figure", *SILENCE_MS["figure"]))
        elif part.startswith("**") and part.endswith("**"):
            inner = part[2:-2]
            if inner.strip() == "gold":
                out.append(Chunk("", "gold", *SILENCE_MS["gold"]))
            else:
                out.append(Chunk(inner, "keyword", *SILENCE_MS["keyword"]))
        elif out and out[-1].kind == "base":
            out[-1] = Chunk(out[-1].text + part, "base", 0, 0)
        else:
            out.append(Chunk(part, "base", 0, 0))
    return out


class WordTiming(NamedTuple):
    word: str
    start_s: float
    end_s: float


def build_word_timings(
    chunks: Sequence[Chunk],
    chunk_events: Sequence[Sequence[WordTiming]],
) -> list[WordTiming]:
    """Rebuild cumulative timings across chunks + inserted silence.

    Each `chunk_events[i]` holds chunk-relative word spans (seconds). Speech chunks
    advance the cursor by their pre/post silence; pause/empty chunks advance by the
    full (pre + post) silence with no words emitted.
    """
    out: list[WordTiming] = []
    cursor = 0.0
    for chunk, events in zip(chunks, chunk_events):
        pre, post = chunk.pre_silence_ms, chunk.post_silence_ms
        if chunk.kind == "pause" or not chunk.text.strip():
            cursor += (pre + post) / 1000.0
            continue
        cursor += pre / 1000.0
        local_end = cursor
        for ev in events:
            start = cursor + ev.start_s
            end = cursor + ev.end_s
            out.append(WordTiming(ev.word, round(start, 3), round(end, 3)))
            local_end = max(local_end, end)
        cursor = local_end + post / 1000.0
    return out


def timing_jsonl(timings: Sequence[WordTiming]) -> str:
    """Serialize word timings to the `.timing.jsonl` contract (`vibe/check.py`)."""
    lines = (
        json.dumps(
            {"word": t.word, "start_s": round(t.start_s, 3), "end_s": round(t.end_s, 3)},
            ensure_ascii=False,
        )
        for t in timings
    )
    return "\n".join(lines) + "\n"


class Synthesizer(Protocol):
    def __call__(self, text: str, *, voice: str, rate: str, volume: str) -> SynthResult: ...


class Encoder(Protocol):
    def __call__(self, units: list[tuple[bytes, int, int]], *, sample_rate: int, channels: int) -> bytes: ...


SynthResult = tuple[bytes, tuple[WordTiming, ...]]


def fake_synthesizer(voice: str = "fake-voice") -> Synthesizer:
    """Deterministic offline synthesizer for tests and the CLI fake seam."""

    def _synth(text: str, *, voice: str = voice, rate: str = "0%", volume: str = "0%") -> SynthResult:
        words: list[WordTiming] = []
        t = 0.0
        for w in text.split():
            words.append(WordTiming(w, round(t, 3), round(t + 0.2, 3)))
            t += 0.25
        return (b"fake-audio", tuple(words))

    return _synth


def fake_encoder() -> Encoder:
    """Deterministic offline encoder for tests and the CLI fake seam."""

    def _enc(units: list[tuple[bytes, int, int]], *, sample_rate: int, channels: int) -> bytes:
        return b"fake-mp3"

    return _enc


class NarrationError(RuntimeError):
    """Raised when real TTS synthesis or the ffmpeg codec fails."""


def _signed_prosody(value: str) -> str:
    """edge-tts requires a signed prosody (`+0%`, `-8%`); our spec's bare `0%` is
    accepted by normalizing to `+0%` so the real synthesizer tolerates it."""
    return value if value[:1] in "+-" else "+" + value


def edge_tts_synthesizer(voice: str = config.NARRATION_VOICE) -> Synthesizer:
    """Real synthesizer via edge-tts (requires network)."""

    import edge_tts

    def _synth(text: str, *, voice: str = voice, rate: str = "0%", volume: str = "0%") -> SynthResult:
        try:
            comm = edge_tts.Communicate(
                text,
                voice,
                rate=_signed_prosody(rate),
                volume=_signed_prosody(volume),
                boundary="WordBoundary",
            )
            audio = bytearray()
            words: list[WordTiming] = []
            for chunk in comm.stream_sync():
                kind = chunk.get("type", "")
                if kind == "audio":
                    audio += chunk.get("data", b"")
                elif kind == "WordBoundary":
                    offset = float(chunk.get("offset", 0)) / 1e7
                    duration = float(chunk.get("duration", 0)) / 1e7
                    words.append(WordTiming(str(chunk.get("text", "")), offset, offset + duration))
            if not audio:
                raise NarrationError("edge-tts returned no audio")
            return (bytes(audio), tuple(words))
        except NarrationError:
            raise
        except Exception as exc:  # network, auth, service errors
            raise NarrationError(f"edge-tts synthesis failed: {exc}") from exc

    return _synth


def _decode_mp3(audio: bytes, sample_rate: int, channels: int) -> bytes:
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", "pipe:0",
                "-f", "s16le", "-ar", str(sample_rate), "-ac", str(channels), "pipe:1",
            ],
            input=audio,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise NarrationError(f"ffmpeg not found: {exc}") from exc
    if proc.returncode != 0:
        raise NarrationError(f"ffmpeg decode failed: {proc.stderr[-300:]}")  # type: ignore[str-bytes-safe]  # bytes repr intended
    return proc.stdout


def ffmpeg_encoder(*, bitrate: str = config.NARRATION_MP3_BITRATE) -> Encoder:
    """Real encoder: mp3 chunks -> s16le PCM -> silence -> concat -> mp3."""

    def _enc(units: list[tuple[bytes, int, int]], *, sample_rate: int, channels: int) -> bytes:
        frame = sample_rate * channels * 2  # bytes per second of s16le PCM
        pcm = bytearray()
        for audio, pre_ms, post_ms in units:
            pre = (frame * pre_ms) // 1000
            post = (frame * post_ms) // 1000
            pcm += b"\x00" * (pre - pre % 2)
            if audio:
                pcm += _decode_mp3(audio, sample_rate, channels)
            pcm += b"\x00" * (post - post % 2)
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-v", "error",
                    "-f", "s16le", "-ar", str(sample_rate), "-ac", str(channels), "-i", "pipe:0",
                    "-c:a", "libmp3lame", "-b:a", bitrate, "-f", "mp3", "pipe:1",
                ],
                input=bytes(pcm),
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise NarrationError(f"ffmpeg not found: {exc}") from exc
        if proc.returncode != 0:
            raise NarrationError(f"ffmpeg encode failed: {proc.stderr[-300:]}")  # type: ignore[str-bytes-safe]  # bytes repr intended
        return proc.stdout

    return _enc


@dataclass(frozen=True)
class SegmentNarration:
    mp3_bytes: bytes
    timings: tuple[WordTiming, ...]


@dataclass(frozen=True)
class SegmentResult:
    index: int
    status: str
    ok: bool
    message: str


def narrate_segment(
    script_text: str,
    *,
    synthesizer: Synthesizer,
    encoder: Encoder,
) -> SegmentNarration:
    """Synthesize one segment's script into audio bytes + cumulative word timing."""
    units: list[tuple[bytes, int, int]] = []
    chunk_events: list[Sequence[WordTiming]] = []
    chunks: list[Chunk] = []
    for line in script_text.splitlines():
        if not line.strip():
            continue
        for chunk in parse_line(line):
            chunks.append(chunk)
            if chunk.kind == "pause" or not chunk.text.strip():
                units.append((b"", chunk.pre_silence_ms, chunk.post_silence_ms))
                chunk_events.append([])
                continue
            rate, volume = KNOBS[chunk.kind]
            audio, words = synthesizer(
                chunk.text, voice=config.NARRATION_VOICE, rate=rate, volume=volume
            )
            units.append((audio, chunk.pre_silence_ms, chunk.post_silence_ms))
            chunk_events.append(words)
    audio_bytes = encoder(
        units, sample_rate=config.AUDIO_SAMPLE_RATE, channels=config.AUDIO_CHANNELS
    )
    timings = tuple(build_word_timings(chunks, chunk_events))
    return SegmentNarration(mp3_bytes=audio_bytes, timings=timings)


def _write_atomic(path: Path, payload: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def narrate_approved(
    lay: layout.Layout,
    *,
    synthesizer: Synthesizer,
    encoder: Encoder,
) -> list[SegmentResult]:
    """Narrate every `approved` segment; skip others; write `.mp3` + `.timing.jsonl`."""
    idx = script.read_index(lay)
    rows = cast(list[object], idx["scripts"])
    results: list[SegmentResult] = []
    for row in rows:
        rec = cast(dict[str, object], row)
        n = int(cast(Any, rec["index"]))
        status = str(rec["status"])
        mp3 = lay.narration / f"segment-{n}.mp3"
        timing = lay.narration / f"segment-{n}.timing.jsonl"
        if status != script.STATUS_APPROVED:
            results.append(SegmentResult(n, status, False, f"segment-{n}.mp3: skipped ({status})"))
            continue
        try:
            text = (lay.scripts / str(rec["file"])).read_text(encoding="utf-8")
            seg = narrate_segment(text, synthesizer=synthesizer, encoder=encoder)
        except (NarrationError, OSError) as exc:
            results.append(SegmentResult(n, status, False, f"segment-{n}.mp3: error: {exc}"))
            continue
        _write_atomic(mp3, seg.mp3_bytes)
        _write_atomic(timing, timing_jsonl(seg.timings).encode("utf-8"))
        results.append(SegmentResult(n, status, True, f"segment-{n}.mp3: OK"))
    return results
