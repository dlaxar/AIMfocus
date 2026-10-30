"""
tests/test_originals.py – passages mapped back onto the original (HTML) letter text.
"""
from __future__ import annotations

import pytest

from app.originals import raw_span, track_normalized
from preprocess.s0_passages import _normalize_text

RAW = ("  ORTXXX, am 15.04.2016<BR><BR><BR>Frau&nbsp;M&uuml;ller<br/>"
       "<b>Diagnosen:</b>\tKM-Allergie &amp; Hypertonie<BR>Thrombo ASS 100mg   1-0-0  ")


def test_tracked_normalization_equals_s0():
    assert "".join(c for c, _, _ in track_normalized(RAW)) == _normalize_text(RAW)


@pytest.mark.parametrize("phrase", ["Frau\xa0Müller", "KM-Allergie & Hypertonie", "Thrombo ASS 100mg 1-0-0", "Diagnosen:"])
def test_every_phrase_maps_back_to_its_raw_text(phrase):
    norm = _normalize_text(RAW)
    start = norm.index(phrase)
    a, b = raw_span(RAW, start, start + len(phrase))
    assert _normalize_text(RAW[a:b]) == phrase


def test_entity_maps_to_the_whole_entity():
    norm = _normalize_text(RAW)
    start = norm.index("ü")
    a, b = raw_span(RAW, start, start + 1)
    assert RAW[a:b] == "&uuml;"


def test_span_outside_the_text_is_rejected():
    assert raw_span(RAW, 0, 10_000) is None


def test_original_endpoints_404_for_unknown(client):
    assert client.get("/api/documents/NOPE/original").status_code == 404
    assert client.get("/api/documents/NOPE/original/file").status_code == 404
    assert client.get("/api/passages/NOPE:p1/original").status_code == 404


@pytest.fixture()
def client():
    import app.store as store_mod
    from fastapi.testclient import TestClient
    from tests.test_smoke import _build_fixture_cache

    _build_fixture_cache()
    store_mod._store = None
    from app.main import app

    with TestClient(app) as c:
        yield c
