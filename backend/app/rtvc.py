"""Client for the RTVC_Stockage API — the platform Kairos indexes on top of.

RTVC owns upload, transcoding/HLS, streaming and auth. Kairos only calls it to:
  - trigger + await HLS transcode (generate-hls / hls-status)
  - fetch the raw media to transcribe/OCR locally (signed-url / stream)
  - obtain a playback URL for the frontend (stream-token)

Several RTVC responses are untyped in the OpenAPI spec, so extraction is
defensive: we try the common field names and expose the raw payload so it can be
adjusted against the live API without code changes elsewhere.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings


class RTVCError(RuntimeError):
    pass


def _first(d: Any, keys: list[str]) -> Any:
    if isinstance(d, dict):
        for k in keys:
            if k in d and d[k] not in (None, ""):
                return d[k]
    return None


# Values RTVC might use for a finished / failed transcode.
_DONE = {"completed", "complete", "ready", "done", "finished", "success", "ok"}
_FAILED = {"error", "failed", "failure", "cancelled", "canceled"}


def normalize_hls_state(payload: Any) -> str:
    """Map an arbitrary hls-status payload to ready|failed|processing|pending."""
    # bare boolean or {"ready": true} / {"hls_ready": true}
    if payload is True:
        return "ready"
    ready_flag = _first(payload, ["ready", "hls_ready", "is_ready", "available"])
    if ready_flag is True:
        return "ready"
    # payload may be a bare string ("completed") or nested under a status key
    raw = payload if isinstance(payload, str) else _first(
        payload, ["status", "state", "hls_status", "phase"]
    )
    if isinstance(raw, str):
        low = raw.lower()
        if low in _DONE:
            return "ready"
        if low in _FAILED:
            return "failed"
        if low in ("pending", "not_started", "queued", "none"):
            return "pending"
        return "processing"
    return "pending"


class RTVCClient:
    def __init__(self) -> None:
        self._client = httpx.Client(
            base_url=settings.rtvc_base_url.rstrip("/"),
            timeout=settings.rtvc_timeout,
            verify=settings.rtvc_verify_ssl,
            follow_redirects=True,
        )
        self._token: str | None = None
        self._lock = threading.Lock()

    # ---- auth -----------------------------------------------------------
    def _login(self) -> str:
        if not settings.rtvc_username or not settings.rtvc_password:
            raise RTVCError("RTVC_USERNAME / RTVC_PASSWORD not configured")
        data = {
            "grant_type": "password",
            "username": settings.rtvc_username,
            "password": settings.rtvc_password,
        }
        params = {}
        if settings.rtvc_otp_code:
            params["otp_code"] = settings.rtvc_otp_code
        resp = self._client.post("/auth/login", data=data, params=params)
        if resp.status_code != 200:
            raise RTVCError(f"login failed: {resp.status_code} {resp.text[:200]}")
        token = resp.json().get("access_token")
        if not token:
            raise RTVCError("login response missing access_token")
        return token

    def _auth_headers(self, force: bool = False) -> dict[str, str]:
        with self._lock:
            if force or self._token is None:
                self._token = self._login()
            return {"Authorization": f"Bearer {self._token}"}

    def _authed(self, method: str, url: str, **kw) -> httpx.Response:
        """Call an authenticated endpoint, refreshing the token once on 401."""
        resp = self._client.request(method, url, headers=self._auth_headers(), **kw)
        if resp.status_code == 401:
            resp = self._client.request(
                method, url, headers=self._auth_headers(force=True), **kw
            )
        return resp

    # ---- endpoints ------------------------------------------------------
    def nas_status(self) -> dict:
        r = self._authed("GET", "/nas/status")
        r.raise_for_status()
        return r.json()

    def library(self) -> Any:
        r = self._authed("GET", "/documents/library")
        r.raise_for_status()
        return r.json()

    def generate_hls(self, media_id: int) -> Any:
        r = self._authed("POST", f"/documents/{media_id}/generate-hls")
        if r.status_code >= 400:
            raise RTVCError(f"generate-hls {media_id}: {r.status_code} {r.text[:200]}")
        try:
            return r.json()
        except ValueError:
            return {"raw": r.text}

    def hls_status(self, media_id: int) -> tuple[str, Any]:
        # hls-status is unauthenticated per the spec.
        r = self._client.get(f"/media/{media_id}/hls-status")
        # RTVC returns 404 ("Aucune génération HLS pour ce média.") when no HLS
        # has been produced yet — that means "pending", not a hard error.
        if r.status_code == 404:
            return "pending", {"detail": r.text}
        r.raise_for_status()
        try:
            payload = r.json()
        except ValueError:
            payload = r.text
        return normalize_hls_state(payload), payload

    def wait_for_hls(self, media_id: int) -> Any:
        """Ensure HLS is ready: trigger generate-hls if needed, then poll."""
        state, payload = self.hls_status(media_id)
        if state == "ready":
            return payload
        if state in ("pending", "processing"):
            # kick off transcode if it hasn't started
            if state == "pending":
                self.generate_hls(media_id)
        deadline = time.time() + settings.hls_poll_timeout
        while time.time() < deadline:
            time.sleep(settings.hls_poll_interval)
            state, payload = self.hls_status(media_id)
            if state == "ready":
                return payload
            if state == "failed":
                raise RTVCError(f"RTVC HLS transcode failed for {media_id}: {payload}")
        raise RTVCError(f"timed out waiting for HLS of media {media_id}")

    def signed_url(self, media_id: int) -> str:
        r = self._authed("GET", f"/media/{media_id}/signed-url")
        r.raise_for_status()
        try:
            payload = r.json()
        except ValueError:
            payload = r.text
        url = _first(payload, ["url", "signed_url", "signedUrl", "download_url", "href"])
        if not url and isinstance(payload, str):
            url = payload
        if not url:
            raise RTVCError(f"no URL in signed-url response: {payload}")
        return url

    def stream_token(self, media_id: int) -> tuple[str, Any]:
        """Return (master_hls_url, raw_payload) for the frontend player."""
        r = self._authed("GET", f"/documents/{media_id}/stream-token")
        r.raise_for_status()
        try:
            payload = r.json()
        except ValueError:
            payload = r.text
        url = _first(payload, ["url", "master_url", "hls_url", "playlist", "stream_url"])
        if not url:
            # some deployments return only a token → build the stream URL
            token = _first(payload, ["token", "stream_token", "access_token"])
            base = settings.rtvc_base_url.rstrip("/")
            url = f"{base}/documents/{media_id}/stream"
            if token:
                url += f"?token={token}"
        return url, payload

    def download_source(self, media_id: int, dest: Path) -> Path:
        """Download the raw media file to ``dest`` for local Vosk/OCR.

        Prefers the signed-url (direct MinIO link); falls back to the RTVC
        stream endpoint. Streams to disk to avoid loading big files in memory.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        # 1. preferred: MinIO signed-url (only works once cached to MinIO)
        try:
            url = self.signed_url(media_id)
            self._stream_to_file(url, dest, authed=False)
            return dest
        except (RTVCError, httpx.HTTPError):
            pass
        # 2. fallback: RTVC raw stream, authorized by a stream token in the URL
        #    (GET /documents/{id}/stream?token=<jwt>). Confirmed field name via
        #    the live API: stream-token returns {"stream_token": "<jwt>"}.
        url, _ = self.stream_token(media_id)
        self._stream_to_file(url, dest, authed=False)
        return dest

    # ---- accès direct au NAS (par chemin de fichier) --------------------
    # Ces deux routes sont celles qui fonctionnent réellement en production :
    # l'API attend les « / » BRUTS dans l'URL (encodés en %2F elle répond
    # « Chemin non absolu ») et, pour /nas/download, le jeton en paramètre
    # ?token= et non dans l'en-tête Authorization.
    def nas_browse(self, path: str = "") -> Any:
        url = f"/nas/browse?path={quote(path, safe='/')}" if path else "/nas/browse"
        r = self._authed("GET", url)
        r.raise_for_status()
        return r.json()

    def download_nas_file(
        self, nas_path: str, dest: Path, max_bytes: int | None = None
    ) -> Path:
        """Télécharge un fichier du NAS via son chemin.

        L'API ignore l'en-tête Range et envoie tout le fichier ; on interrompt
        donc côté client après ``max_bytes`` — suffisant pour indexer un extrait
        sans rapatrier plusieurs centaines de Mo.
        """
        with self._lock:
            if self._token is None:
                self._token = self._login()
            token = self._token
        url = f"/nas/download?path={quote(nas_path, safe='/')}&token={token}"

        dest.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with self._client.stream("GET", url, timeout=None) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise RTVCError(
                    f"nas/download {nas_path}: {resp.status_code} {resp.text[:200]}"
                )
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                    fh.write(chunk)
                    written += len(chunk)
                    if max_bytes is not None and written >= max_bytes:
                        break
        if written == 0:
            raise RTVCError(f"nas/download {nas_path}: fichier vide")
        return dest

    def _stream_to_file(self, url: str, dest: Path, authed: bool) -> None:
        headers = self._auth_headers() if authed else None
        with self._client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                    fh.write(chunk)


_client_singleton: RTVCClient | None = None


def get_rtvc() -> RTVCClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = RTVCClient()
    return _client_singleton
