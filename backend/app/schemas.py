from datetime import datetime

from pydantic import BaseModel


class MediaOut(BaseModel):
    rtvc_id: int
    title: str | None
    source: str
    status: str
    has_playback: bool
    hls_ready: bool
    duration_ms: int | None
    language: str | None
    error: str | None
    processed_at: datetime | None

    class Config:
        from_attributes = True


class WebhookPayload(BaseModel):
    media_id: int
    title: str | None = None


class ProcessResponse(BaseModel):
    rtvc_id: int
    task_id: str
    status: str


class SearchHit(BaseModel):
    rtvc_id: int
    title: str | None
    source: str  # 'audio' | 'visual'
    start_ms: int
    start_seconds: float
    end_ms: int
    text: str
    score: float           # cosine similarity in [0, 1], higher is better
    # Pertinence issue de la fusion hybride, relative au meilleur résultat.
    # C'est elle qu'affiche l'interface : contrairement au cosinus, elle décroît
    # toujours avec le rang.
    relevance: float = 1.0
    # 'sens' = trouvé par proximité sémantique, 'mots' = trouvé par le texte
    # exact seul (nom propre, sigle, chiffre). Affiché pour que l'utilisateur
    # comprenne pourquoi un résultat au score bas figure quand même en tête.
    matched: str = "sens"
    # Une vignette n'existe que si Kairos possede le fichier video en local
    # (copie de lecture). Les resultats venant de la base externe de
    # transcription designent des medias dont Kairos n'a AUCUN fichier : le
    # frontend n'affiche donc pas d'image pour eux, au lieu de demander une
    # vignette qui repondra 404.
    has_thumbnail: bool = False
    deep_link: str         # /video/<rtvc_id>?t=<seconds>


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


class StreamTokenResponse(BaseModel):
    rtvc_id: int
    master_url: str        # HLS URL to hand to Video.js
    raw: dict              # raw RTVC payload (field names may vary)
