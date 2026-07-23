"""Vosk transcription. Produces word-level timestamps, then groups words into
readable segments used for both display and embedding."""

from __future__ import annotations

import json
import wave
from dataclasses import dataclass
from pathlib import Path

from vosk import KaldiRecognizer, Model, SetLogLevel

from app.config import settings

SetLogLevel(-1)  # silence Vosk's noisy stderr

_model: Model | None = None


def _get_model() -> Model:
    global _model
    if _model is None:
        _model = Model(settings.vosk_model_path)
    return _model


@dataclass
class Segment:
    start_ms: int
    end_ms: int
    text: str


def _iter_words(wav_path: Path):
    """Yield {'word', 'start', 'end'} dicts from Vosk over the whole file."""
    wf = wave.open(str(wav_path), "rb")
    if wf.getnchannels() != 1 or wf.getsampwidth() != 2 or wf.getframerate() != 16000:
        raise ValueError("WAV must be 16 kHz, mono, 16-bit PCM for Vosk")

    rec = KaldiRecognizer(_get_model(), wf.getframerate())
    rec.SetWords(True)

    def _emit(result_json: str):
        res = json.loads(result_json)
        for w in res.get("result", []):
            yield w

    while True:
        data = wf.readframes(4000)
        if len(data) == 0:
            break
        if rec.AcceptWaveform(data):
            yield from _emit(rec.Result())
    yield from _emit(rec.FinalResult())
    wf.close()


def transcribe(wav_path: Path) -> list[Segment]:
    """Group Vosk words into segments split on long pauses or max duration."""
    max_len = settings.transcript_max_segment_seconds
    max_gap = settings.transcript_gap_seconds

    segments: list[Segment] = []
    cur_words: list[str] = []
    cur_start: float | None = None
    prev_end: float | None = None

    def flush(end: float | None):
        nonlocal cur_words, cur_start
        if cur_words and cur_start is not None and end is not None:
            segments.append(
                Segment(
                    start_ms=int(cur_start * 1000),
                    end_ms=int(end * 1000),
                    text=" ".join(cur_words),
                )
            )
        cur_words = []
        cur_start = None

    for w in _iter_words(wav_path):
        start, end, word = w["start"], w["end"], w["word"]
        if cur_start is None:
            cur_start = start
        gap = start - prev_end if prev_end is not None else 0.0
        too_long = (end - cur_start) >= max_len
        if cur_words and (gap >= max_gap or too_long):
            flush(prev_end)
            cur_start = start
        cur_words.append(word)
        prev_end = end

    flush(prev_end)
    return segments
