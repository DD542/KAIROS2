"""Tests unitaires de la logique pure du client RTVC (sans réseau)."""

import base64
import json
import time

from app.rtvc import RTVCClient, _first, normalize_hls_state


def test_normalize_hls_state():
    assert normalize_hls_state({"status": "completed"}) == "ready"
    assert normalize_hls_state({"state": "processing"}) == "processing"
    assert normalize_hls_state({"hls_status": "pending"}) == "pending"
    assert normalize_hls_state({"status": "error"}) == "failed"
    assert normalize_hls_state({"ready": True}) == "ready"
    assert normalize_hls_state({"hls_ready": True}) == "ready"
    assert normalize_hls_state(True) == "ready"
    assert normalize_hls_state({"status": "queued"}) == "pending"
    assert normalize_hls_state({}) == "pending"
    assert normalize_hls_state("completed") == "ready"  # chaîne nue


def test_first_extracts_common_field_names():
    assert _first({"url": "http://a"}, ["url", "signed_url"]) == "http://a"
    assert _first({"signed_url": "http://b"}, ["url", "signed_url"]) == "http://b"
    assert _first({"other": 1}, ["url"]) is None
    assert _first("pas un dict", ["url"]) is None
    assert _first({"url": ""}, ["url"]) is None  # vide ignoré


def _make_jwt(exp: float) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


def test_jwt_exp_parsing():
    future = time.time() + 3600
    assert abs(RTVCClient._jwt_exp(_make_jwt(future)) - future) < 1
    assert RTVCClient._jwt_exp("pas-un-jwt") == 0.0
