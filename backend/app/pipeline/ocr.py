"""Tesseract OCR over extracted keyframes. Consecutive identical/near-empty
frames are collapsed so a static slide yields one row, not dozens."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytesseract
from PIL import Image

MIN_CHARS = 3  # ignore frames whose OCR yields almost nothing


@dataclass
class OcrItem:
    timestamp_ms: int
    text: str


def _clean(raw: str) -> str:
    # collapse whitespace, drop control chars
    text = re.sub(r"\s+", " ", raw).strip()
    return text


def ocr_keyframes(frames: list[tuple[int, Path]]) -> list[OcrItem]:
    items: list[OcrItem] = []
    last_text: str | None = None
    for ts_ms, path in frames:
        try:
            raw = pytesseract.image_to_string(Image.open(path), lang="fra")
        except Exception:
            continue
        text = _clean(raw)
        if len(text) < MIN_CHARS:
            continue
        # dedupe when the same slide is shown across several keyframes
        if last_text is not None and text == last_text:
            continue
        items.append(OcrItem(timestamp_ms=ts_ms, text=text))
        last_text = text
    return items
