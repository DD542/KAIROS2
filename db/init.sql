-- Kairos schema (V2 — semantic-search layer over RTVC).
-- Kairos stores ONLY AI-derived data. Rows are keyed by rtvc_id = RTVC media_id.
-- Vector dimension 384 = sentence-transformers/all-MiniLM-L6-v2.

CREATE EXTENSION IF NOT EXISTS vector;

-- Idempotence ledger: which RTVC media Kairos has already indexed.
CREATE TABLE IF NOT EXISTS processed_media (
    rtvc_id      BIGINT PRIMARY KEY,               -- media_id (RTVC id, or local id)
    title        TEXT,
    source       TEXT NOT NULL DEFAULT 'rtvc',     -- rtvc | local
    local_path   TEXT,                             -- source file when source='local'
    playback_path TEXT,                            -- transcoded MP4 served to the player
    hls_ready    BOOLEAN NOT NULL DEFAULT FALSE,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending|processing|ready|failed
    error        TEXT,
    duration_ms  BIGINT,
    language     TEXT,                             -- langue détectée (auto)
    processed_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS transcriptions (
    id       BIGSERIAL PRIMARY KEY,
    rtvc_id  BIGINT NOT NULL,
    start_ms BIGINT NOT NULL,
    end_ms   BIGINT NOT NULL,
    text     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transcriptions_rtvc ON transcriptions(rtvc_id);

CREATE TABLE IF NOT EXISTS ocr_texts (
    id           BIGSERIAL PRIMARY KEY,
    rtvc_id      BIGINT NOT NULL,
    timestamp_ms BIGINT NOT NULL,
    text         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ocr_rtvc ON ocr_texts(rtvc_id);

-- Unified hybrid index: audio (Vosk) + visual (OCR) vectors in one table,
-- both pointing at a timestamp in the same RTVC media.
CREATE TABLE IF NOT EXISTS embeddings (
    id        BIGSERIAL PRIMARY KEY,
    rtvc_id   BIGINT NOT NULL,
    source    TEXT NOT NULL CHECK (source IN ('audio', 'visual')),
    start_ms  BIGINT NOT NULL,
    end_ms    BIGINT NOT NULL,
    text      TEXT NOT NULL,
    embedding vector(384) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_embeddings_rtvc ON embeddings(rtvc_id);

-- Index ANN pour la similarité cosinus. HNSW (et non ivfflat) : il renvoie
-- toujours des voisins quel que soit le nombre de lignes — ivfflat avec des
-- listes vides pouvait renvoyer zéro résultat sur un petit volume. HNSW est
-- aussi plus rapide en requête à grande échelle.
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw
    ON embeddings USING hnsw (embedding vector_cosine_ops);
