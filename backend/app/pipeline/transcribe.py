"""Transcription audio avec faster-whisper.

Remplace Vosk : bien plus précis sur du français réel (parole, événementiel,
chant), tout en restant local et CPU (quantification int8 via CTranslate2).
Un filtre de détection de voix (VAD) écarte les silences.

Le contrat de sortie est identique (``list[Segment]`` avec start/end en ms), donc
le reste du pipeline (indexation, embeddings) est inchangé.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from faster_whisper import WhisperModel

from app.config import settings

_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(
            settings.whisper_model,
            device="cpu",
            compute_type=settings.whisper_compute_type,
        )
    return _model


@dataclass
class Segment:
    start_ms: int
    end_ms: int
    text: str


def transcribe(wav_path: Path) -> tuple[list[Segment], str | None]:
    """Transcrit l'audio puis regroupe les segments en passages cohérents.

    Renvoie ``(segments, langue_detectee)``. La langue est détectée
    automatiquement (Whisper est multilingue) sauf si ``WHISPER_LANGUAGE`` la
    force : une vidéo en anglais est donc correctement transcrite, et la
    recherche reste cross-langue grâce aux embeddings multilingues.

    faster-whisper renvoie des segments courts (phrases) ; on les fusionne en
    passages d'environ ``transcript_max_segment_seconds`` (coupés sur les
    silences), plus parlants à la recherche qu'un fragment de trois mots.
    """
    model = _get_model()
    raw_segments, info = model.transcribe(
        str(wav_path),
        language=settings.whisper_language or None,  # None = détection auto
        vad_filter=True,
        beam_size=1,  # rapide ; suffisant en CPU
    )
    detected = getattr(info, "language", None)

    max_len = settings.transcript_max_segment_seconds
    max_gap = settings.transcript_gap_seconds

    segments: list[Segment] = []
    cur_words: list[str] = []
    cur_start: float | None = None
    prev_end: float | None = None

    def flush(end: float | None) -> None:
        nonlocal cur_words, cur_start
        text = " ".join(w.strip() for w in cur_words).strip()
        if text and cur_start is not None and end is not None:
            segments.append(
                Segment(start_ms=int(cur_start * 1000), end_ms=int(end * 1000), text=text)
            )
        cur_words = []
        cur_start = None

    for seg in raw_segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        if cur_start is None:
            cur_start = seg.start
        gap = seg.start - prev_end if prev_end is not None else 0.0
        too_long = (seg.end - cur_start) >= max_len
        if cur_words and (gap >= max_gap or too_long):
            flush(prev_end)
            cur_start = seg.start
        cur_words.append(text)
        prev_end = seg.end

    flush(prev_end)
    return segments, detected
