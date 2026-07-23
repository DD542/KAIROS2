#!/usr/bin/env python3
"""Diagnostic RTVC pour Kairos — teste en une commande si l'API RTVC et son NAS
sont fonctionnels pour l'indexation.

Aucune dépendance externe (bibliothèque standard Python uniquement).

Usage :
    # identifiants via variables d'environnement (recommandé)
    export RTVC_USERNAME=... RTVC_PASSWORD=...
    python scripts/rtvc_diag.py --media-id 1

    # ou en arguments
    python scripts/rtvc_diag.py -u Rtvc2026 -p '******' --media-id 1

    # options
    --base-url URL     défaut https://api.rtvc-media.com
    --media-id N       média à tester (défaut : le premier de la bibliothèque)
    --browse CHEMIN    chemin NAS à explorer (défaut /)
    --trigger-hls      déclenche POST generate-hls (action, pas seulement lecture)
    --upload FICHIER   teste un upload réel (écriture NAS)

Le script affiche un rapport lisible avec ✓ / ✗ et un verdict final.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

# Windows consoles default to cp1252 and can't print ✓/✗ — force UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

OK = "\033[92m✓\033[0m"
KO = "\033[91m✗\033[0m"
WARN = "\033[93m!\033[0m"


class Diag:
    def __init__(self, base_url: str, verify_ssl: bool = True):
        self.base = base_url.rstrip("/")
        self.token: str | None = None
        self.ctx = None if verify_ssl else ssl._create_unverified_context()
        self.results: list[tuple[str, bool, str]] = []

    # ---- HTTP helper (stdlib) ------------------------------------------
    def _req(self, method, path, headers=None, data=None, want_bytes=False, max_read=None):
        url = path if path.startswith("http") else self.base + path
        req = urllib.request.Request(url, method=method, data=data)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=40, context=self.ctx) as r:
                body = r.read(max_read) if max_read else r.read()
                return r.status, dict(r.headers), body if want_bytes else body.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = e.read()
            return e.code, dict(e.headers), body.decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            return 0, {}, f"{type(e).__name__}: {e}"

    def _json(self, text):
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001
            return None

    def record(self, name, ok, detail=""):
        self.results.append((name, ok, detail))
        mark = OK if ok else KO
        print(f"  {mark} {name}" + (f"  — {detail}" if detail else ""))

    def auth_headers(self):
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    # ---- checks --------------------------------------------------------
    def login(self, user, pwd):
        print("\n[1] Authentification (OAuth2 /auth/login)")
        data = urllib.parse.urlencode(
            {"grant_type": "password", "username": user, "password": pwd}
        ).encode()
        code, _, text = self._req(
            "POST", "/auth/login",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data,
        )
        j = self._json(text)
        if code == 200 and j and j.get("access_token"):
            self.token = j["access_token"]
            self.record("login", True, f"user={j.get('username')} token_type={j.get('token_type')}")
            return True
        self.record("login", False, f"HTTP {code} {text[:150]}")
        return False

    def nas_status(self):
        print("\n[2] État du NAS (/nas/status)")
        code, _, text = self._req("GET", "/nas/status", headers=self.auth_headers())
        j = self._json(text) or {}
        connected = bool(j.get("connected"))
        self.record("nas/status", code == 200 and connected,
                    f"HTTP {code} connected={j.get('connected')} host={j.get('host')} shares={j.get('shares_count')}")

    def browse(self, path):
        print(f"\n[3] Exploration NAS (/nas/browse?path={path})")
        q = urllib.parse.urlencode({"path": path}) if path else ""
        code, _, text = self._req("GET", f"/nas/browse{('?' + q) if q else ''}", headers=self.auth_headers())
        j = self._json(text) or {}
        items = j.get("items", []) if isinstance(j, dict) else []
        names = [f"{'[D]' if it.get('isdir') else '   '} {it.get('name')}" for it in items]
        ok = code == 200
        self.record(f"browse {path or '/'}", ok, f"HTTP {code}" + (f" — {len(items)} éléments" if ok else f" {text[:120]}"))
        for n in names[:20]:
            print(f"        {n}")
        return names

    def library(self):
        print("\n[4] Bibliothèque (/documents/library)")
        code, _, text = self._req("GET", "/documents/library", headers=self.auth_headers())
        j = self._json(text) or {}
        results = j.get("results", []) if isinstance(j, dict) else []
        self.record("library", code == 200, f"HTTP {code} — {j.get('count', len(results))} médias")
        for it in results[:15]:
            print(f"        id={it.get('id'):>3}  storage={str(it.get('storage_status')):<10} "
                  f"cached={str(it.get('is_cached')):<5} hls={str(it.get('hls_path')):<6} "
                  f"title={it.get('title')!r}")
        return results

    def media_checks(self, media_id, trigger_hls):
        print(f"\n[5] Accès au contenu du média id={media_id}")

        # 5a. hls-status (non authentifié)
        code, _, text = self._req("GET", f"/media/{media_id}/hls-status")
        # 404 = "pas encore de HLS" (normal), 200 = état renvoyé
        self.record("hls-status", code in (200, 404),
                    f"HTTP {code} {self._json(text) or text[:100]}")

        # 5b. signed-url (MinIO)
        code, _, text = self._req("GET", f"/media/{media_id}/signed-url", headers=self.auth_headers())
        self.record("signed-url (MinIO)", code == 200, f"HTTP {code} {text[:120]}")

        # 5c. stream-token (lecture)
        code, _, text = self._req("GET", f"/documents/{media_id}/stream-token", headers=self.auth_headers())
        j = self._json(text) or {}
        stok = j.get("stream_token") or j.get("token")
        self.record("stream-token", code == 200 and bool(stok), f"HTTP {code}")

        # 5d. téléchargement réel de quelques octets (le vrai test NAS en lecture)
        if stok:
            url = f"{self.base}/documents/{media_id}/stream?token={stok}"
            code, hdrs, body = self._req("GET", url, headers={"Range": "bytes=0-65535"},
                                         want_bytes=True, max_read=65536)
            got = len(body) if isinstance(body, (bytes, bytearray)) else 0
            self.record("téléchargement octets bruts", code in (200, 206) and got > 0,
                        f"HTTP {code} type={hdrs.get('Content-Type')} octets={got}")

        # 5e. generate-hls (action — écriture/transcodage)
        if trigger_hls:
            code, _, text = self._req("POST", f"/documents/{media_id}/generate-hls", headers=self.auth_headers())
            self.record("generate-hls (action)", code < 400, f"HTTP {code} {text[:120]}")

    def upload_test(self, filepath, title="Kairos Diag Upload"):
        print(f"\n[6] Test d'upload (écriture NAS) : {filepath}")
        if not os.path.isfile(filepath):
            self.record("upload", False, "fichier introuvable")
            return
        with open(filepath, "rb") as f:
            content = f.read()
        boundary = uuid.uuid4().hex
        fname = os.path.basename(filepath)
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{fname}"\r\n'
            f"Content-Type: video/mp4\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        q = urllib.parse.urlencode({"title": title, "privacy": "private"})
        headers = self.auth_headers()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        code, _, text = self._req("POST", f"/documents/upload?{q}", headers=headers, data=body)
        self.record("upload", code < 400, f"HTTP {code} {text[:150]}")

    # ---- verdict -------------------------------------------------------
    def verdict(self):
        print("\n" + "=" * 60)
        api_ok = any(n == "login" and ok for n, ok, _ in self.results)
        content_ok = any(n == "téléchargement octets bruts" and ok for n, ok, _ in self.results)
        n_ok = sum(1 for _, ok, _ in self.results if ok)
        print(f"RÉSULTAT : {n_ok}/{len(self.results)} tests OK")
        if not api_ok:
            print(f"{KO} L'API RTVC n'est pas joignable / identifiants invalides.")
        elif content_ok:
            print(f"{OK} NAS OPÉRATIONNEL — le contenu est accessible. Kairos peut indexer.")
        else:
            print(f"{WARN} API OK mais le NAS ne sert pas les octets vidéo "
                  "(lecture/écriture fichiers en échec). À corriger côté RTVC/NAS "
                  "avant que Kairos puisse traiter les médias.")
        print("=" * 60)


def main():
    ap = argparse.ArgumentParser(description="Diagnostic RTVC pour Kairos")
    ap.add_argument("--base-url", default=os.getenv("RTVC_BASE_URL", "https://api.rtvc-media.com"))
    ap.add_argument("-u", "--username", default=os.getenv("RTVC_USERNAME"))
    ap.add_argument("-p", "--password", default=os.getenv("RTVC_PASSWORD"))
    ap.add_argument("--media-id", type=int, default=None)
    ap.add_argument("--browse", default="", help="chemin NAS à explorer (vide = racine)")
    ap.add_argument("--trigger-hls", action="store_true")
    ap.add_argument("--upload", default=None)
    ap.add_argument("--no-verify-ssl", action="store_true")
    args = ap.parse_args()

    if not args.username or not args.password:
        print("Erreur : fournir --username/--password ou RTVC_USERNAME/RTVC_PASSWORD.")
        sys.exit(2)

    print(f"Diagnostic RTVC → {args.base_url}")
    d = Diag(args.base_url, verify_ssl=not args.no_verify_ssl)

    if not d.login(args.username, args.password):
        d.verdict()
        sys.exit(1)

    d.nas_status()
    d.browse(args.browse)
    media = d.library()

    media_id = args.media_id
    if media_id is None and media:
        media_id = media[0].get("id")
    if media_id is not None:
        d.media_checks(media_id, args.trigger_hls)
    else:
        print("\n[5] (aucun média à tester)")

    if args.upload:
        d.upload_test(args.upload)

    d.verdict()


if __name__ == "__main__":
    main()
