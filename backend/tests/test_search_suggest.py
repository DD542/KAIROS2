"""Tests des parties pures de la suggestion et de la normalisation des limites.

Elles n'ont besoin ni de base ni de modèle : ce sont exactement les endroits où
une régression passerait inaperçue (un extrait coupé au milieu d'un mot, un 0
retombant sur une valeur par défaut et tronquant la vidéo).
"""

from app.routes.ingest import _no_limit
from app.search import _SUGGEST_MAX_CHARS, _phrase_around


def test_phrase_starts_on_the_matched_word():
    text = "et donc je vous parle aujourd'hui de la grâce de Dieu"
    assert _phrase_around(text, "grâce").startswith("grâce")


def test_phrase_never_cuts_mid_word():
    text = "mot " * 60
    out = _phrase_around(text, "mot")
    assert len(out) <= _SUGGEST_MAX_CHARS + 1  # +1 : le caractère « … »
    assert not out.endswith("mo…")


def test_phrase_falls_back_when_term_absent():
    assert _phrase_around("bonjour tout le monde", "absent") == "bonjour tout le monde"


def test_phrase_collapses_whitespace():
    assert _phrase_around("  a\n\n  b  ", "a") == "a b"


def test_phrase_handles_empty_text():
    assert _phrase_around("", "x") == ""


def test_zero_means_no_limit():
    """0 = « vidéo entière ». C'est la régression qui tronquait la lecture."""
    assert _no_limit(0) is None
    assert _no_limit(None) is None
    assert _no_limit(-5) is None
    assert _no_limit(120) == 120
