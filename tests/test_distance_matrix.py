"""Tests del módulo distance_matrix. HTTP siempre mockeado."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from src import config, distance_matrix as dm
from src.exceptions import MatrixProviderError


# ---- Helpers ---------------------------------------------------------------

DEPOT = (config.DEPOT_LAT, config.DEPOT_LNG)
A = (41.4500, 2.2474)  # Barcelona aprox
B = (41.9332, 2.2664)  # Vic
C = (41.5167, 2.4500)  # Mataró aprox
COORDS3 = [DEPOT, A, B]


def _ok_response(durations, distances) -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {
        "code": "Ok",
        "durations": durations,
        "distances": distances,
    }
    return r


# ---- Caché ------------------------------------------------------------------

def test_load_distance_cache_empty(tmp_path):
    assert dm.load_distance_cache(tmp_path / "no.parquet") == {}


def test_persist_and_load_cache_roundtrip(tmp_path):
    cache = {(41.5, 2.2, 41.9, 2.3): (1234.0, 56789.0)}
    out = tmp_path / "dm.parquet"
    dm._persist_cache(cache, "osrm", out)
    rt = dm.load_distance_cache(out)
    assert rt == cache


# ---- Provider resolution ----------------------------------------------------

def test_resolve_provider_explicit():
    assert dm.resolve_provider("ors") == "ors"
    assert dm.resolve_provider("OSRM") == "osrm"


def test_resolve_provider_invalid_raises():
    with pytest.raises(MatrixProviderError):
        dm.resolve_provider("google")


def test_resolve_provider_env_overrides_default(monkeypatch):
    monkeypatch.setenv("MATRIX_PROVIDER", "osrm")
    monkeypatch.setenv("ORS_API_KEY", "abc")
    assert dm.resolve_provider() == "osrm"


def test_resolve_provider_uses_ors_when_key_present(monkeypatch):
    monkeypatch.delenv("MATRIX_PROVIDER", raising=False)
    monkeypatch.setenv("ORS_API_KEY", "abc")
    assert dm.resolve_provider() == "ors"


def test_resolve_provider_default_osrm(monkeypatch):
    monkeypatch.delenv("MATRIX_PROVIDER", raising=False)
    monkeypatch.delenv("ORS_API_KEY", raising=False)
    assert dm.resolve_provider() == "osrm"


# ---- Haversine --------------------------------------------------------------

def test_haversine_zero_for_same_point():
    assert dm.haversine_m(A, A) == pytest.approx(0.0, abs=1e-3)


def test_haversine_known_distance():
    """Mollet (≈41.54N) ↔ Vic (≈41.93N) ≈ 44 km en línea recta."""
    d = dm.haversine_m((41.5408, 2.2128), (41.9332, 2.2664))
    assert 40_000 <= d <= 50_000


# ---- get_matrix: end-to-end con OSRM mockeado -------------------------------

def test_get_matrix_osrm_basic(tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_PROVIDER", "osrm")
    monkeypatch.delenv("ORS_API_KEY", raising=False)

    durations = [[0, 100, 200], [110, 0, 90], [210, 95, 0]]
    distances = [[0, 1000, 2000], [1100, 0, 900], [2100, 950, 0]]
    fake = _ok_response(durations, distances)

    cache = tmp_path / "dm.parquet"
    with patch("src.distance_matrix.requests.request", return_value=fake) as call:
        t, d = dm.get_matrix(COORDS3, cache_path=cache)

    assert t.shape == (3, 3)
    assert d.shape == (3, 3)
    assert t[0, 1] == 100
    assert d[1, 2] == 900
    call.assert_called_once()
    assert call.call_args.args[0] == "GET"
    assert "table/v1/driving" in call.call_args.args[1]


def test_get_matrix_second_call_hits_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_PROVIDER", "osrm")
    durations = [[0, 100, 200], [110, 0, 90], [210, 95, 0]]
    distances = [[0, 1000, 2000], [1100, 0, 900], [2100, 950, 0]]

    cache = tmp_path / "dm.parquet"
    with patch("src.distance_matrix.requests.request", return_value=_ok_response(durations, distances)) as call:
        dm.get_matrix(COORDS3, cache_path=cache)
        assert call.call_count == 1
        # Segunda llamada con las mismas coords no debe tocar red.
        t, d = dm.get_matrix(COORDS3, cache_path=cache)
        assert call.call_count == 1
    assert t[0, 1] == 100
    assert d[1, 2] == 900


def test_get_matrix_fills_null_pair_with_haversine(tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_PROVIDER", "osrm")
    durations = [[0, None, 200], [110, 0, 90], [210, 95, 0]]
    distances = [[0, None, 2000], [1100, 0, 900], [2100, 950, 0]]
    fake = _ok_response(durations, distances)
    cache = tmp_path / "dm.parquet"
    with patch("src.distance_matrix.requests.request", return_value=fake):
        t, d = dm.get_matrix(COORDS3, cache_path=cache)

    expected_dist = dm.haversine_m(DEPOT, A) * config.HAVERSINE_DETOUR_FACTOR
    assert d[0, 1] == pytest.approx(expected_dist, rel=1e-6)
    assert t[0, 1] == pytest.approx(expected_dist / config.URBAN_AVG_SPEED_MS, rel=1e-6)
    assert d[0, 2] == 2000  # los demás permanecen


def test_get_matrix_ors_uses_post_and_authorization(tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_PROVIDER", "ors")
    monkeypatch.setenv("ORS_API_KEY", "FAKEKEY")
    durations = [[0, 100], [110, 0]]
    distances = [[0, 1000], [1100, 0]]
    fake = MagicMock(status_code=200)
    fake.json.return_value = {"durations": durations, "distances": distances}
    cache = tmp_path / "dm.parquet"
    with patch("src.distance_matrix.requests.request", return_value=fake) as call:
        t, _ = dm.get_matrix([DEPOT, A], cache_path=cache)
    assert t.shape == (2, 2)
    args = call.call_args
    assert args.args[0] == "POST"
    assert config.ORS_MATRIX_URL in args.args[1]
    assert args.kwargs["headers"]["Authorization"] == "FAKEKEY"
    body = args.kwargs["json"]
    assert body["locations"][0] == [DEPOT[1], DEPOT[0]]  # lng,lat order
    assert "duration" in body["metrics"] and "distance" in body["metrics"]


def test_get_matrix_retries_on_429(tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_PROVIDER", "osrm")
    rate_limited = MagicMock(status_code=429)
    durations = [[0, 100], [110, 0]]
    distances = [[0, 1000], [1100, 0]]
    ok = _ok_response(durations, distances)

    cache = tmp_path / "dm.parquet"
    with patch("src.distance_matrix.requests.request", side_effect=[rate_limited, ok]):
        with patch("src.distance_matrix.time.sleep"):  # no esperar back-off real
            t, d = dm.get_matrix([DEPOT, A], cache_path=cache)
    assert t.shape == (2, 2)


def test_get_matrix_raises_on_provider_error_response(tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_PROVIDER", "osrm")
    fake = MagicMock(status_code=200)
    fake.json.return_value = {"code": "InvalidValue", "message": "bad input"}
    cache = tmp_path / "dm.parquet"
    with patch("src.distance_matrix.requests.request", return_value=fake):
        with pytest.raises(MatrixProviderError):
            dm.get_matrix([DEPOT, A], cache_path=cache)


def test_get_matrix_raises_on_dim_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("MATRIX_PROVIDER", "osrm")
    bad = _ok_response([[0]], [[0]])
    cache = tmp_path / "dm.parquet"
    with patch("src.distance_matrix.requests.request", return_value=bad):
        with pytest.raises(MatrixProviderError):
            dm.get_matrix([DEPOT, A], cache_path=cache)


def test_get_matrix_empty_coords_raises():
    with pytest.raises(MatrixProviderError):
        dm.get_matrix([])


# ---- Asimetría preservada ---------------------------------------------------

def test_get_matrix_can_be_asymmetric(tmp_path, monkeypatch):
    """Aceptamos asimetría (calles unidireccionales)."""
    monkeypatch.setenv("MATRIX_PROVIDER", "osrm")
    durations = [[0, 100], [200, 0]]
    distances = [[0, 1000], [1500, 0]]
    fake = _ok_response(durations, distances)
    cache = tmp_path / "dm.parquet"
    with patch("src.distance_matrix.requests.request", return_value=fake):
        t, d = dm.get_matrix([DEPOT, A], cache_path=cache)
    assert t[0, 1] != t[1, 0]
    assert d[0, 1] != d[1, 0]
