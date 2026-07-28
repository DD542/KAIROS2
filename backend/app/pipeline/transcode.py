"""Local FFmpeg helpers used by the AI pipeline.

In V2, RTVC owns HLS transcoding — Kairos never builds HLS. It only needs local
FFmpeg to derive inputs for Vosk and Tesseract from the downloaded source file:
duration probe, 16 kHz mono WAV (audio), and periodic keyframes.
"""

from __future__ import annotations

import json
import re
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


def _scene_keyframes(src: Path, out_dir: Path) -> list[tuple[int, Path]]:
    """Une image par CHANGEMENT DE PLAN, avec son horodatage réel.

    Sur une vidéo entière, échantillonner à intervalle fixe produit des
    milliers d'images quasi identiques : l'OCR y passe l'essentiel du temps de
    traitement pour ne lire que des répétitions du même texte. Découper sur les
    ruptures visuelles donne une image par contenu distinct — c'est là que se
    trouve le texte à l'écran.

    ``showinfo`` écrit l'instant de chaque image retenue sur stderr ; on le lit
    pour horodater précisément, au lieu de supposer un pas régulier.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    threshold = settings.scene_change_threshold
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-vf", f"select='gt(scene,{threshold})',showinfo",
            "-vsync", "vfr", "-q:v", "3",
            str(out_dir / "kf_%06d.jpg"),
        ],
        capture_output=True, text=True,
    )
    # Une vidéo sans rupture franche (plan fixe, caméra unique) ne déclenche
    # aucune sélection : ffmpeg sort alors en erreur « no packets ». Ce n'est
    # pas une panne, c'est un cas normal — on renvoie une liste vide et
    # l'appelant retombe sur l'échantillonnage régulier.
    if proc.returncode != 0 and not any(out_dir.glob("kf_*.jpg")):
        return []

    times = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr or "")]
    frames: list[tuple[int, Path]] = []
    for idx, path in enumerate(sorted(out_dir.glob("kf_*.jpg"))):
        # si showinfo n'a pas donné autant d'instants que d'images, on retombe
        # sur une estimation régulière plutôt que de perdre l'image
        ts_ms = int(times[idx] * 1000) if idx < len(times) else idx * 1000
        target = out_dir / f"frame_{ts_ms}.jpg"
        path.rename(target)
        frames.append((ts_ms, target))
    return frames


def _interval_keyframes(src: Path, out_dir: Path) -> list[tuple[int, Path]]:
    """Une image toutes les ``keyframe_interval_seconds`` (échantillonnage fixe)."""
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


def extract_keyframes(src: Path, out_dir: Path) -> list[tuple[int, Path]]:
    """Images à soumettre à l'OCR, nommées frame_<timestamp_ms>.jpg.

    Par défaut, découpage sur changement de plan ; repli sur l'échantillonnage
    régulier si la détection ne rend rien (plan-séquence fixe, diaporama sans
    rupture nette) pour ne jamais perdre le texte à l'écran.
    """
    if not settings.keyframe_scene_detect:
        return _interval_keyframes(src, out_dir)
    try:
        frames = _scene_keyframes(src, out_dir)
    except Exception:  # noqa: BLE001 - jamais au prix de perdre le texte à l'écran
        frames = []
    if frames:
        return frames
    for leftover in out_dir.glob("*.jpg"):
        leftover.unlink(missing_ok=True)
    return _interval_keyframes(src, out_dir)
