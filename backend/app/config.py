from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://kairos:kairos@localhost:5433/kairos"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # ---- Base externe "Transcription Pipeline" (Mike, Supabase, lecture seule) ----
    # C'est en réalité la base RTVC_Stockage elle-même (vérifié : public.medias.id
    # == media_id RTVC, champ pour champ identique à /documents/library) avec un
    # schéma `transcription` ajouté par-dessus. Kairos ne transcrit plus lui-même :
    # la recherche interroge cette base EN DIRECT à chaque requête (l'analyse
    # texte/segments se fait côté Postgres), rien n'est copié ni stocké localement.
    transcription_db_dsn: str = (
        "host=aws-1-eu-central-2.pooler.supabase.com port=5432 dbname=postgres "
        "user=transcription_reader.qaqlqxdrxrguuhjikkth password=password2026 "
        "sslmode=require"
    )

    data_dir: str = "/data"
    frontend_dir: str = "/frontend"
    # Dossier (monté en lecture seule) où déposer des vidéos à indexer localement
    media_input_dir: str = "/media_in"

    # ---- RTVC_Stockage API (the platform Kairos indexes on top of) ----
    rtvc_base_url: str = "https://api.rtvc-media.com"
    rtvc_username: str = ""
    rtvc_password: str = ""
    rtvc_otp_code: str = ""            # optional 2FA code for /auth/login
    rtvc_verify_ssl: bool = True
    rtvc_timeout: float = 30.0
    # Exploration du NAS : opération interactive, on abandonne vite pour ne
    # jamais dépasser le délai d'un proxy (qui renverrait une page HTML).
    rtvc_browse_timeout: float = 8.0
    # HLS readiness polling
    hls_poll_interval: float = 5.0
    hls_poll_timeout: float = 900.0    # 15 min max wait for RTVC transcode

    # ---- Local AI pipeline ----
    # Transcription faster-whisper. "small" = bon compromis qualité/vitesse CPU
    # ("base" plus rapide/moins précis, "medium" plus précis/plus lent).
    whisper_model: str = "small"
    whisper_compute_type: str = "int8"  # quantifié pour la vitesse CPU
    # Langue de transcription : vide = détection automatique (recommandé,
    # gère les vidéos en anglais, espagnol…). Mettre "fr" pour forcer.
    whisper_language: str = ""
    # Modèle MULTILINGUE : indispensable pour du contenu français. Même
    # dimension (384) que all-MiniLM-L6-v2, donc schéma pgvector inchangé.
    #
    # Pour une précision nettement supérieure, basculer sur un modèle de la
    # famille E5 — même dimension 384, donc AUCUN changement de schéma :
    #   EMBEDDING_MODEL=intfloat/multilingual-e5-small
    #   EMBEDDING_QUERY_PREFIX="query: "
    #   EMBEDDING_PASSAGE_PREFIX="passage: "
    # puis POST /admin/reindex-embeddings (ré-encode le texte déjà transcrit,
    # sans repasser par ffmpeg ni Whisper). Les préfixes sont OBLIGATOIRES avec
    # E5 : sans eux, le modèle perd une large part de sa qualité.
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dim: int = 384
    embedding_query_prefix: str = ""
    embedding_passage_prefix: str = ""

    # Recherche hybride : combine le vectoriel (sens) et le lexical (mots).
    # À laisser activée — c'est elle qui rattrape noms propres, sigles et
    # chiffres, invisibles pour la recherche purement sémantique.
    search_hybrid: bool = True

    # Seuil de similarité optionnel (paramètre ?min_score= de /search).
    # Désactivé par défaut : sur ce modèle multilingue, même un texte non
    # pertinent obtient ~0.4, donc un seuil séparerait mal le bon du bruit ;
    # c'est le filtre anti-bruit à l'indexation qui assure la qualité.
    search_min_score: float = 0.0

    # Taille téléchargée en 1re passe pour un média RTVC : suffit pour la
    # plupart des fichiers ; si le transcodage échoue (mp4 tronqué), le worker
    # rapatrie le fichier entier automatiquement.
    download_probe_mb: int = 300

    # ---- Indexation automatique (mode « bibliothèque déjà remplie ») --------
    # Quand Kairos est installé sur un système qui possède DÉJÀ les vidéos, on
    # ne veut pas cliquer « Indexer » une par une : un balayage périodique met
    # en file tout ce qui n'est pas encore indexé. L'indexation manuelle reste
    # disponible (bouton par vidéo) — les deux voies partagent le même coeur.
    autosync_enabled: bool = False
    autosync_interval_minutes: int = 15
    # Balaye le dossier local monté (MEDIA_INPUT_DIR).
    autosync_local: bool = True
    # Balaye le NAS RTVC à partir de ce dossier. Vide = pas de balayage RTVC
    # (le scan RTVC est coûteux : ne l'activer qu'avec une racine précise).
    autosync_rtvc_root: str = ""
    # Plafond de mises en file par passage : évite de saturer la file au 1er
    # démarrage sur une bibliothèque de milliers de vidéos.
    autosync_batch: int = 20

    keyframe_interval_seconds: int = 3
    # Extraction des images pour l'OCR. Depuis que l'on indexe les vidéos
    # ENTIÈRES, l'échantillonnage régulier produit des milliers d'images
    # quasi identiques : on découpe sur les changements de plan. Seuil entre 0
    # et 1 — plus bas = plus d'images (plus sensible).
    keyframe_scene_detect: bool = True
    scene_change_threshold: float = 0.35
    # Confiance minimale (0-100) d'un mot lu à l'écran pour être indexé.
    # Tesseract n'avoue jamais son ignorance : sans ce seuil, il produit des
    # suites de lettres inventées qui polluent le classement de recherche.
    ocr_min_confidence: float = 60.0
    transcript_max_segment_seconds: float = 8.0
    transcript_gap_seconds: float = 0.8

    # Shared secret guarding the RTVC -> Kairos webhook (optional).
    webhook_secret: str = ""

    # Mot de passe d'accès à TOUTE l'application (optionnel).
    # Vide = accès libre (dev local). Défini = chaque requête exige le mot de
    # passe (HTTP Basic, le navigateur affiche une petite fenêtre de connexion).
    # Indispensable dès que Kairos est exposé sur Internet : l'explorateur RTVC
    # utilise les identifiants NAS configurés côté serveur.
    kairos_password: str = ""

    # Origines autorisées à appeler l'API depuis un autre site (CORS), séparées
    # par des virgules. Vide = aucune (recommandé) : l'interface Kairos est
    # servie par le même serveur, elle n'a pas besoin de CORS.
    cors_origins: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
