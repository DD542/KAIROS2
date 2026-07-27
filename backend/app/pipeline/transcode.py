"""Local FFmpeg helpers used by the AI pipeline.

In V2, RTVC owns HLS transcoding — Kairos never builds HLS. It only needs local
FFmpeg to derive inputs for Vosk and Tesseract from the downloaded source file:
duration probe, 16 kHz mono WAV (audio), and periodic keyframes.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.config import settings


def _run(cmd: list[str]) -> None:
    """Exécute une commande et, en cas d'échec, remonte la fin de la sortie
    d'erreur (indispensable pour diagnostiquer FFmpeg)."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = " | ".join(err[-3:]) if err else f"code {proc.returncode}"
        raise RuntimeError(f"ffmpeg a échoué ({cmd[0]}): {tail}")


def probe_duration_ms(src: Path) -> int:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(src),
        ],
        check=True, capture_output=True, text=True,
    ).stdout
    duration = float(json.loads(out)["format"]["duration"])
    return int(duration * 1000)


def extract_audio_wav(src: Path, out_path: Path) -> Path:
    """Extract 16 kHz mono PCM WAV — the format Vosk expects."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", str(src),
        "-ar", "16000", "-ac", "1", "-vn",
        "-f", "wav", str(out_path),
    ])
    return out_path


def make_playback_mp4(src: Path, out_path: Path, max_seconds: int | None = None) -> Path:
    """Transcode to a browser-friendly MP4 (H.264 + AAC, 720p max).

    ``faststart`` moves the index to the front so the player can seek before the
    whole file is downloaded. Used for locally-ingested media, where Kairos has
    to serve the video itself instead of delegating to RTVC.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # -err_detect ignore_err + discardcorrupt : tolère un fichier légèrement
    # abîmé (ex. téléchargement écourté) au lieu d'abandonner.
    cmd = ["ffmpeg", "-y", "-err_detect", "ignore_err", "-fflags", "+discardcorrupt"]
    if max_seconds:
        cmd += ["-t", str(max_seconds)]
    cmd += [
        "-i", str(src),
        "-vf", "scale=-2:'min(720,ih)'",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "+faststart",
        str(out_path),
    ]
    _run(cmd)
    return out_path


def make_thumbnail(src: Path, out_path: Path, at_seconds: float, width: int = 320) -> Path:
    """Extrait une image de la vidéo à un instant donné (vignette d'aperçu).

    ``-ss`` avant ``-i`` = recherche rapide (seek sur les images clés), ce qui
    rend la génération quasi instantanée même sur une vidéo longue.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y",
        "-ss", f"{max(at_seconds, 0):.3f}",
        "-i", str(src),
        "-frames:v", "1",
        "-vf", f"scale={width}:-2",
        "-q:v", "5",
        str(out_path),
    ])
    return out_path


def extract_keyframes(src: Path, out_dir: Path) -> list[tuple[int, Path]]:
    """Extract one frame every ``keyframe_interval_seconds``.

    Frames are named frame_<timestamp_ms>.jpg. Returns [(timestamp_ms, path)].
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    interval = settings.keyframe_interval_seconds
    fps = 1.0 / interval

    _run([
        "ffmpeg", "-y", "-i", str(src),
        "-vf", f"fps={fps}",
        "-q:v", "3",
        str(out_dir / "kf_%06d.jpg"),
    ])

    frames: list[tuple[int, Path]] = []
    for idx, path in enumerate(sorted(out_dir.glob("kf_*.jpg"))):
        ts_ms = int(idx * interval * 1000)
        target = out_dir / f"frame_{ts_ms}.jpg"
        path.rename(target)
        frames.append((ts_ms, target))
    return frames
