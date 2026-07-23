from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://kairos:kairos@localhost:5433/kairos"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

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
    # HLS readiness polling
    hls_poll_interval: float = 5.0
    hls_poll_timeout: float = 900.0    # 15 min max wait for RTVC transcode

    # ---- Local AI pipeline ----
    vosk_model_path: str = "/opt/models/vosk-fr"
    # Modèle MULTILINGUE : indispensable pour du contenu français. Même
    # dimension (384) que all-MiniLM-L6-v2, donc schéma pgvector inchangé.
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    embedding_dim: int = 384

    # Seuil de similarité optionnel (paramètre ?min_score= de /search).
    # Désactivé par défaut : sur ce modèle multilingue, même un texte non
    # pertinent obtient ~0.4, donc un seuil séparerait mal le bon du bruit ;
    # c'est le filtre anti-bruit à l'indexation qui assure la qualité.
    search_min_score: float = 0.0

    keyframe_interval_seconds: int = 3
    transcript_max_segment_seconds: float = 8.0
    transcript_gap_seconds: float = 0.8

    # Shared secret guarding the RTVC -> Kairos webhook (optional).
    webhook_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
