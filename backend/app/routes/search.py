"""Semantic search endpoint (the public face of Kairos)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import SearchResponse
from app.search import search as run_search
from app.search import suggest as run_suggest

router = APIRouter(tags=["search"])


@router.get("/suggest")
def suggest(
    q: str = Query(..., min_length=2, description="Début de question"),
    limit: int = Query(8, ge=1, le=20),
    db: Session = Depends(get_db),
):
    """Complétions proposées pendant la frappe.

    Renvoie des extraits RÉELLEMENT présents dans la bibliothèque : l'utilisateur
    voit tout de suite ce qu'il peut trouver, au lieu de deviner une formulation.
    """
    return {"query": q, "suggestions": run_suggest(db, q, limit=limit)}


@router.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., min_length=1, description="Question en langage naturel"),
    media_id: int | None = Query(None, description="Restreindre à un media RTVC"),
    limit: int = Query(10, ge=1, le=50),
    min_score: float | None = Query(None, ge=0.0, le=1.0, description="Score minimum (défaut config)"),
    db: Session = Depends(get_db),
):
    hits = run_search(db, q, limit=limit, media_id=media_id, min_score=min_score)
    return SearchResponse(query=q, hits=hits)
