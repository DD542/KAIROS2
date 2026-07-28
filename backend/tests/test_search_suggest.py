"""Tests des parties pures : suggestion, normalisation des limites et chemins.

Ce sont les endroits où une régression passerait inaperçue — un extrait coupé
au milieu d'un mot, un 0 retombant sur une valeur par défaut et tronquant la
vidéo, une racine NAS renvoyée sous une forme que RTVC refuse.
"""

import pytest

from app.lang import FALLBACK, pg_config
from app.routes.ingest import _no_limit
from app.rtvc import RTVCClient
from app.search import _SUGGEST_MAX_CHARS, _fold, _fold_map, _phrase_around, _starts_a_word


# --- extraits de suggestion --------------------------------------------------

def test_phrase_shows_context_before_the_match():
    """Un terme en fin de phrase doit rester lisible : sans contexte amont, la
    suggestion se réduisait au seul mot déjà tapé."""
    out = _phrase_around("Que la grâce de Dieu soit dans notre famille", "famille")
    assert "famille" in out
    assert out.startswith("…")  # du contexte a été repris avant le terme
    assert len(out.split()) >= 3


def test_phrase_never_cuts_mid_word():
    out = _phrase_around("mot " * 60, "mot")
    assert len(out) <= _SUGGEST_MAX_CHARS + 2  # marge pour les « … »
    assert "mo…" not in out


def test_phrase_falls_back_when_term_absent():
    assert _phrase_around("bonjour tout le monde", "absent") == "bonjour tout le monde"


def test_phrase_collapses_whitespace():
    assert _phrase_around("  a\n\n  b  ", "a") == "a b"


def test_phrase_handles_empty_text():
    assert _phrase_around("", "x") == ""


# --- insensibilité aux accents ----------------------------------------------

def test_fold_removes_accents():
    assert _fold("Grâce à Noël") == "grace a noel"


def test_fold_map_indexes_back_to_the_original():
    """La table d'index doit rester juste même quand un caractère se déplie en
    plusieurs (ligature « œ ») — sinon l'extrait est décalé."""
    src = "cœur brisé"
    folded, idx = _fold_map(src)
    assert folded == "coeur brise"
    # le « b » de « brise » dans la version repliée pointe sur le « b » d'origine
    assert src[idx[folded.index("b")]] == "b"


def test_phrase_finds_unaccented_query():
    out = _phrase_around("Que toute la grâce de Dieu", "grace")
    assert "grâce" in out


# --- frontière de mot --------------------------------------------------------

def test_word_boundary_rejects_inner_match():
    """« dieu » ne doit pas proposer « adieu » : l'utilisateur ne reconnaît pas
    sa frappe au milieu d'un autre mot."""
    assert not _starts_a_word("je te dis adieu", "dieu")


def test_word_boundary_accepts_real_word():
    assert _starts_a_word("la grâce de Dieu", "dieu")


def test_word_boundary_ignores_accents():
    assert _starts_a_word("Que toute la grâce", "grace")


# --- limites de durée / taille ----------------------------------------------

@pytest.mark.parametrize("given,expected", [(0, None), (None, None), (-5, None), (120, 120)])
def test_zero_means_no_limit(given, expected):
    """0 = « vidéo entière ». C'est la régression qui tronquait la lecture."""
    assert _no_limit(given) is expected or _no_limit(given) == expected


# --- chemins NAS -------------------------------------------------------------

@pytest.mark.parametrize("given", ["/", "", "  ", "//"])
def test_nas_root_variants_collapse_to_empty(given):
    """RTVC renvoie « / » comme parent mais refuse « / » en entrée (502) :
    c'est ce qui cassait le bouton « Dossier parent »."""
    assert RTVCClient._normalize_nas_path(given) == ""


def test_nas_path_loses_trailing_slash():
    assert RTVCClient._normalize_nas_path("/ACCES/Rtvc2026/") == "/ACCES/Rtvc2026"


def test_nas_path_kept_intact():
    assert RTVCClient._normalize_nas_path("/ACCES/Rtvc2026") == "/ACCES/Rtvc2026"


# --- analyseur lexical par langue -------------------------------------------

def test_known_language_maps_to_its_analyzer():
    assert pg_config("fr") == "french"
    assert pg_config("en-US") == "english"


def test_unknown_language_falls_back():
    assert pg_config("ja") == FALLBACK
    assert pg_config(None) == FALLBACK
