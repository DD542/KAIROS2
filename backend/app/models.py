from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, DateTime, Index, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.db import Base

# Kairos stores ONLY the AI-derived data. Everything else (the media file,
# metadata, HLS, streaming, auth) lives in RTVC. Rows are keyed by ``rtvc_id``
# = the RTVC media_id (integer).


class ProcessedMedia(Base):
    """Idempotence ledger: what Kairos has already indexed.

    ``source`` distinguishes media pulled from RTVC ('rtvc') from files ingested
    directly from disk ('local') — the local path exists so the pipeline can be
    demonstrated end-to-end without depending on RTVC's storage backend.
    """

    __tablename__ = "processed_media"

    rtvc_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # media_id
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(Text, default="rtvc")  # rtvc | local
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    playback_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    hls_ready: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(Text, default="pending")  # pending|processing|ready|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)  # détectée
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def has_playback(self) -> bool:
        """Vrai si Kairos peut servir la vidéo lui-même (fichier transcodé)."""
        return bool(self.playback_path)


class Transcription(Base):
    __tablename__ = "transcriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rtvc_id: Mapped[int] = mapped_column(BigInteger, index=True)
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)


class OcrText(Base):
    __tablename__ = "ocr_texts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rtvc_id: Mapped[int] = mapped_column(BigInteger, index=True)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)


class Embedding(Base):
    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    rtvc_id: Mapped[int] = mapped_column(BigInteger, index=True)
    source: Mapped[str] = mapped_column(Text)  # 'audio' | 'visual'
    start_ms: Mapped[int] = mapped_column(BigInteger)
    end_ms: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(settings.embedding_dim))


# HNSW cosine index for ANN search. HNSW always returns nearest neighbours
# (ivfflat could return nothing when a probed list was empty on small data)
# and is faster at scale.
Index(
    "idx_embeddings_hnsw",
    Embedding.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
)
