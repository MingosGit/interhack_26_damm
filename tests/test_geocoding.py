"""Tests del módulo de geocoding. Nominatim siempre va mockeado en unit tests."""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from geopy.exc import GeocoderTimedOut

from src import config, geocoding


# ---- helpers ----------------------------------------------------------------

def _loc(lat: float, lng: float):
    return SimpleNamespace(latitude=lat, longitude=lng)


@pytest.fixture(autouse=True)
def _reset_throttle_and_geocoder():
    """Reinicia el throttle y el singleton Nominatim entre tests."""
    geocoding._THROTTLE._last_call = 0.0
    geocoding._GEOCODER = None
    yield


# ---- caché ------------------------------------------------------------------

def test_load_cache_returns_empty_when_missing(tmp_path):
    df = geocoding.load_geocoding_cache(tmp_path / "no.parquet")
    assert df.empty
    assert list(df.columns) == geocoding.CACHE_COLUMNS


def test_load_cache_roundtrip(tmp_path):
    out = tmp_path / "g.parquet"
    df = pd.DataFrame([{
        "cliente_id": 1, "lat": 41.5, "lng": 2.2,
        "status": "ok", "query_used": "X", "reason": "",
    }])
    geocoding._persist_cache(df, out)
    rt = geocoding.load_geocoding_cache(out)
    assert len(rt) == 1
    assert rt.iloc[0]["lat"] == pytest.approx(41.5)


# ---- geocode_address: success / no result / retry ---------------------------

def test_geocode_address_success_with_mock():
    fake = MagicMock()
    fake.geocode.return_value = _loc(41.5, 2.2)
    with patch.object(geocoding, "_get_geocoder", return_value=fake):
        out = geocoding.geocode_address("Carrer Llevant 2, 08110 MONTCADA, Catalunya, España")
    assert out == (41.5, 2.2)
    fake.geocode.assert_called_once()


def test_geocode_address_returns_none_when_no_match():
    fake = MagicMock()
    fake.geocode.return_value = None
    with patch.object(geocoding, "_get_geocoder", return_value=fake):
        assert geocoding.geocode_address("Calle inventada 123") is None


def test_geocode_address_retries_on_timeout_then_succeeds():
    fake = MagicMock()
    fake.geocode.side_effect = [GeocoderTimedOut("nope"), _loc(41.4, 2.1)]
    with patch.object(geocoding, "_get_geocoder", return_value=fake):
        # Acortamos el back-off para que el test no tarde 2 s
        with patch("src.geocoding.time.sleep") as fake_sleep:
            out = geocoding.geocode_address("X", max_retries=1)
    assert out == (41.4, 2.1)
    assert fake.geocode.call_count == 2
    assert fake_sleep.called


# ---- rate-limit -------------------------------------------------------------

def test_throttle_enforces_min_interval():
    """Dos llamadas seguidas deben separarse por al menos NOMINATIM_MIN_INTERVAL_S."""
    fake = MagicMock()
    fake.geocode.side_effect = [_loc(41.5, 2.2), _loc(41.6, 2.3)]
    with patch.object(geocoding, "_get_geocoder", return_value=fake):
        t0 = time.monotonic()
        geocoding.geocode_address("A")
        geocoding.geocode_address("B")
        elapsed = time.monotonic() - t0
    assert elapsed >= config.NOMINATIM_MIN_INTERVAL_S - 0.05, \
        f"throttle no respetado: {elapsed:.3f}s"


# ---- helpers de query -------------------------------------------------------

def test_build_full_query_format():
    q = geocoding.build_full_query("Carrer Major 5", "08110", "MONTCADA")
    assert q == "Carrer Major 5, 08110 MONTCADA, Catalunya, España"


def test_in_catalunya_bbox():
    assert geocoding._in_catalunya(41.54, 2.21)  # Mollet
    assert not geocoding._in_catalunya(40.41, -3.70)  # Madrid


# ---- geocode_all: idempotencia + fallback CP --------------------------------

def test_geocode_all_skips_already_ok_in_cache(tmp_path):
    out = tmp_path / "g.parquet"
    addrs = pd.DataFrame([{
        "cliente_id": 7, "calle": "X", "cp": "08110", "poblacion": "Y",
        "cliente_nombre": "Z",
    }])
    pd.DataFrame([{
        "cliente_id": 7, "lat": 41.5, "lng": 2.2,
        "status": "ok", "query_used": "old", "reason": "",
    }]).to_parquet(out, engine="pyarrow", index=False)

    fake = MagicMock()
    fake.geocode.side_effect = AssertionError("no debería llamar a Nominatim")
    with patch.object(geocoding, "_get_geocoder", return_value=fake):
        result = geocoding.geocode_all(addrs, cache_path=out)
    assert len(result) == 1
    assert result.iloc[0]["query_used"] == "old"
    fake.geocode.assert_not_called()


def test_geocode_all_falls_back_to_cp_when_full_address_fails(tmp_path):
    out = tmp_path / "g.parquet"
    addrs = pd.DataFrame([{
        "cliente_id": 9, "calle": "Calle inventada S/N", "cp": "08560",
        "poblacion": "MANLLEU", "cliente_nombre": "Test",
    }])

    fake = MagicMock()
    # Primero falla la dirección completa, luego éxito en el CP centroid.
    fake.geocode.side_effect = [None, _loc(42.00, 2.28)]
    with patch.object(geocoding, "_get_geocoder", return_value=fake):
        result = geocoding.geocode_all(addrs, cache_path=out)

    assert fake.geocode.call_count == 2
    row = result.iloc[0]
    assert row["status"] == "ok_cp_fallback"
    assert row["lat"] == pytest.approx(42.00)
    assert "cp_fallback_used" in row["reason"]


def test_geocode_all_marks_failed_when_everything_fails(tmp_path):
    out = tmp_path / "g.parquet"
    addrs = pd.DataFrame([{
        "cliente_id": 11, "calle": "?", "cp": "08000", "poblacion": "?",
        "cliente_nombre": "T",
    }])
    fake = MagicMock()
    fake.geocode.side_effect = [None, None]
    with patch.object(geocoding, "_get_geocoder", return_value=fake):
        result = geocoding.geocode_all(addrs, cache_path=out)
    row = result.iloc[0]
    assert row["status"] == "failed"
    assert pd.isna(row["lat"])
    assert "cp_fallback_failed" in row["reason"]


def test_geocode_all_rejects_out_of_bbox_and_uses_cp_fallback(tmp_path):
    """Si Nominatim devuelve coords fuera de Cataluña, intentar fallback."""
    out = tmp_path / "g.parquet"
    addrs = pd.DataFrame([{
        "cliente_id": 13, "calle": "Calle Mayor 1", "cp": "08110",
        "poblacion": "MONTCADA", "cliente_nombre": "T",
    }])
    fake = MagicMock()
    # Primer hit cae en Madrid (out of bbox), segundo hit es válido.
    fake.geocode.side_effect = [_loc(40.4, -3.7), _loc(41.5, 2.2)]
    with patch.object(geocoding, "_get_geocoder", return_value=fake):
        result = geocoding.geocode_all(addrs, cache_path=out)
    row = result.iloc[0]
    assert row["status"] == "ok_cp_fallback"
    assert "out_of_bbox" in row["reason"]
