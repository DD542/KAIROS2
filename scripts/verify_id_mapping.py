"""Vérifie l'hypothèse : public.medias.id (base transcription) == media_id RTVC.

Compare la bibliothèque RTVC réelle (API, authentifiée) aux lignes de
public.medias (base Supabase lecture seule) sur les mêmes id : si les titres/
chemins correspondent, la jointure directe par id est fiable. Aucune écriture
nulle part (RTVC: GET only ; Supabase: SELECT only).
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import psycopg
from app.rtvc import get_rtvc

DSN = (
    "host=aws-1-eu-central-2.pooler.supabase.com "
    "port=5432 dbname=postgres "
    "user=transcription_reader.qaqlqxdrxrguuhjikkth "
    "password=password2026 "
    "sslmode=require"
)


def main() -> None:
    rtvc = get_rtvc()
    lib = rtvc.library()
    print("=== RTVC /documents/library (brut, tronqué) ===")
    print(str(lib)[:2000])

    items = lib.get("results", lib.get("items", lib)) if isinstance(lib, dict) else lib
    if not isinstance(items, list):
        print("Format inattendu, impossible d'extraire une liste d'items.")
        return

    ids = []
    for it in items:
        if isinstance(it, dict):
            mid = it.get("id") or it.get("media_id") or it.get("document_id")
            if mid:
                ids.append((mid, it.get("title") or it.get("name") or it.get("filename")))
    print("\nIDs RTVC trouvés :", ids)

    if not ids:
        print("Aucun ID exploitable dans la réponse RTVC — comparaison impossible.")
        return

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        print("\n=== Comparaison avec public.medias (même id) ===")
        for rtvc_id, rtvc_title in ids:
            cur.execute(
                "SELECT id, title, nas_path FROM public.medias WHERE id = %s",
                (rtvc_id,),
            )
            row = cur.fetchone()
            print(f"RTVC id={rtvc_id} title={rtvc_title!r}  ->  medias row: {row}")


if __name__ == "__main__":
    main()
