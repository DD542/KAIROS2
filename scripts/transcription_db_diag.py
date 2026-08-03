"""Diagnostic de la base externe 'Transcription Pipeline' (Supabase, lecture seule).

But : comprendre la structure réelle de public.medias / transcription.transcripts
avant de décider comment relier ces lignes aux médias RTVC connus de Kairos
(nas_path, title, ou autre). Ne modifie rien (SELECT uniquement).

Usage : python scripts/transcription_db_diag.py
"""
from __future__ import annotations
import psycopg

DSN = (
    "host=aws-1-eu-central-2.pooler.supabase.com "
    "port=5432 dbname=postgres "
    "user=transcription_reader.qaqlqxdrxrguuhjikkth "
    "password=password2026 "
    "sslmode=require"
)


def main() -> None:
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            print("=== Comptages globaux ===")
            cur.execute("SELECT COUNT(*) FROM public.medias")
            print("medias total:", cur.fetchone()[0])

            cur.execute(
                "SELECT status, COUNT(*) FROM transcription.transcripts "
                "GROUP BY status ORDER BY 2 DESC"
            )
            print("transcripts par statut:", cur.fetchall())

            cur.execute(
                "SELECT language, COUNT(*) FROM transcription.transcripts "
                "WHERE status='done' GROUP BY language"
            )
            print("langues (done):", cur.fetchall())

            print("\n=== Colonnes réelles de public.medias ===")
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='medias' ORDER BY ordinal_position"
            )
            for row in cur.fetchall():
                print(" ", row)

            print("\n=== Échantillon de 15 nas_path (medias liés à une transcription 'done') ===")
            cur.execute(
                "SELECT m.id, m.title, m.nas_path "
                "FROM public.medias m "
                "JOIN transcription.transcripts t ON t.media_id = m.id "
                "WHERE t.status = 'done' "
                "ORDER BY m.id LIMIT 15"
            )
            for row in cur.fetchall():
                print(" ", row)

            print("\n=== Échantillon de 10 nas_path SANS transcription (pour voir la diversité des chemins) ===")
            cur.execute("SELECT id, title, nas_path FROM public.medias ORDER BY random() LIMIT 10")
            for row in cur.fetchall():
                print(" ", row)

            print("\n=== Racines de nas_path les plus fréquentes ===")
            cur.execute(
                "SELECT split_part(nas_path, '/', 2) AS racine, COUNT(*) "
                "FROM public.medias WHERE nas_path IS NOT NULL "
                "GROUP BY 1 ORDER BY 2 DESC LIMIT 15"
            )
            for row in cur.fetchall():
                print(" ", row)


if __name__ == "__main__":
    main()
