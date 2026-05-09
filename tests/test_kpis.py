"""Tests del módulo de KPIs (MVP 8)."""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src import config, kpis
from src.vrp_solver import Solution, Stop


# ---- _movimientos_descarga ------------------------------------------------

def test_movimientos_descarga_uses_n_materiales_per_stop():
    stops = [
        Stop(cliente_id=1, lat=0, lng=0, volumen_l=10, peso_kg=5,
             materiales=[{"material": "A"}, {"material": "B"}]),
        Stop(cliente_id=2, lat=0, lng=0, volumen_l=10, peso_kg=5,
             materiales=[{"material": "C"}]),
    ]
    # 2 + 1 = 3 mats. Real = 3 × 1.5 = 4.5 → 4 (banker rounding). Opt = 3 × 1 = 3.
    real = kpis._movimientos_descarga(stops, 1.5)
    opt = kpis._movimientos_descarga(stops, 1.0)
    assert real == 4
    assert opt == 3


def test_movimientos_descarga_treats_empty_as_one():
    stops = [Stop(cliente_id=1, lat=0, lng=0, volumen_l=10, peso_kg=5)]
    assert kpis._movimientos_descarga(stops, 1.0) == 1


# ---- _pct_change -----------------------------------------------------------

def test_pct_change_basic():
    assert kpis._pct_change(80, 100) == -20.0
    assert kpis._pct_change(120, 100) == 20.0


def test_pct_change_zero_base_returns_zero():
    assert kpis._pct_change(50, 0) == 0.0


# ---- compare con transporte mockeado --------------------------------------

@pytest.fixture
def mini_dataset():
    """Dataset mínimo con 1 transporte de 3 paradas geocodificadas."""
    canonical = pd.DataFrame([
        {"transporte": 999, "fecha": pd.Timestamp("2026-02-02"),
         "ruta": "DR0001", "repartidor": 1, "repartidor_nombre": "X",
         "entrega_id": 10, "cliente_id": 100, "cliente_nombre": "A",
         "calle": "X", "cp": "08001", "poblacion": "BARCELONA", "zona_dd": "DD1",
         "volumen_total_l": 100.0, "peso_total_kg": 50.0,
         "volumen_retornable_l": 60.0, "n_materiales": 2, "n_lineas": 2,
         "materiales_json": '[{"material":"M1","uma":"CAJ","vol_l":50,"peso_kg":25,"cantidad":1,"retornable":false},{"material":"M2","uma":"BRL","vol_l":50,"peso_kg":25,"cantidad":1,"retornable":true}]',
         "pct_retornable": 0.6},
        {"transporte": 999, "fecha": pd.Timestamp("2026-02-02"),
         "ruta": "DR0001", "repartidor": 1, "repartidor_nombre": "X",
         "entrega_id": 20, "cliente_id": 200, "cliente_nombre": "B",
         "calle": "Y", "cp": "08002", "poblacion": "BARCELONA", "zona_dd": "DD1",
         "volumen_total_l": 200.0, "peso_total_kg": 100.0,
         "volumen_retornable_l": 100.0, "n_materiales": 1, "n_lineas": 1,
         "materiales_json": '[{"material":"M3","uma":"CAJ","vol_l":200,"peso_kg":100,"cantidad":1,"retornable":false}]',
         "pct_retornable": 0.0},
        {"transporte": 999, "fecha": pd.Timestamp("2026-02-02"),
         "ruta": "DR0001", "repartidor": 1, "repartidor_nombre": "X",
         "entrega_id": 30, "cliente_id": 300, "cliente_nombre": "C",
         "calle": "Z", "cp": "08003", "poblacion": "BARCELONA", "zona_dd": "DD1",
         "volumen_total_l": 50.0, "peso_total_kg": 25.0,
         "volumen_retornable_l": 0.0, "n_materiales": 1, "n_lineas": 1,
         "materiales_json": '[{"material":"M4","uma":"CAJ","vol_l":50,"peso_kg":25,"cantidad":1,"retornable":false}]',
         "pct_retornable": 0.0},
    ])
    geocoding = pd.DataFrame([
        {"cliente_id": 100, "lat": 41.5, "lng": 2.20, "status": "ok",
         "query_used": "", "reason": ""},
        {"cliente_id": 200, "lat": 41.6, "lng": 2.30, "status": "ok",
         "query_used": "", "reason": ""},
        {"cliente_id": 300, "lat": 41.7, "lng": 2.40, "status": "ok",
         "query_used": "", "reason": ""},
    ])
    return canonical, geocoding


def _fake_matrix(coords):
    """Distancia/tiempo proporcional al índice (matriz simétrica simple)."""
    n = len(coords)
    t = np.full((n, n), 600, dtype=np.int64); np.fill_diagonal(t, 0)
    d = np.full((n, n), 5000, dtype=np.int64); np.fill_diagonal(d, 0)
    return t, d


def test_compare_returns_kpi_for_real_transport(mini_dataset):
    canonical, geocoding = mini_dataset
    with patch("src.kpis.distance_matrix.get_matrix", side_effect=lambda c: _fake_matrix(c)):
        kpi = kpis.compare(999, time_limit_s=2,
                            canonical=canonical, geocoding=geocoding)
    assert kpi.transporte_id == 999
    assert kpi.fecha == date(2026, 2, 2)
    assert kpi.n_paradas == 3
    assert kpi.real_distancia_m > 0
    assert kpi.real_tiempo_s > 0
    assert kpi.opt_distancia_m > 0
    assert kpi.opt_tiempo_s > 0
    # n_movimientos: 4 mats × 1.5 = 6 vs 4 × 1.0 = 4
    assert kpi.real_n_movimientos_descarga == 6
    assert kpi.opt_n_movimientos_descarga == 4
    # delta movimientos: (4-6)/6 = -33.33%
    assert kpi.delta_movimientos_pct == pytest.approx(-33.33, abs=0.1)
    # retornables: real 75%, opt = computed (likely 100% en este caso pequeño)
    assert kpi.real_pct_retornables_recogidos == pytest.approx(0.75)
    assert 0.0 <= kpi.opt_pct_retornables_recogidos <= 1.0
    assert kpi.delta_retornables_pp >= 0  # mejora vs heurística baseline


def test_compare_unknown_transport_raises():
    from src.exceptions import DammSmartTruckError
    canonical = pd.DataFrame(columns=[
        "transporte", "fecha", "entrega_id", "cliente_id", "cliente_nombre",
        "poblacion", "cp", "zona_dd", "volumen_total_l", "peso_total_kg",
        "volumen_retornable_l", "materiales_json",
    ])
    geocoding = pd.DataFrame(columns=["cliente_id", "lat", "lng", "status",
                                       "query_used", "reason"])
    with pytest.raises(DammSmartTruckError):
        kpis.compare(123, canonical=canonical, geocoding=geocoding)


def test_compare_no_geocoding_returns_no_stops_status():
    canonical = pd.DataFrame([{
        "transporte": 5, "fecha": pd.Timestamp("2026-02-02"),
        "ruta": "X", "repartidor": 1, "repartidor_nombre": "X",
        "entrega_id": 1, "cliente_id": 999, "cliente_nombre": "X",
        "calle": "X", "cp": "08001", "poblacion": "X", "zona_dd": "DD1",
        "volumen_total_l": 10.0, "peso_total_kg": 5.0,
        "volumen_retornable_l": 0.0, "n_materiales": 1, "n_lineas": 1,
        "materiales_json": "[]", "pct_retornable": 0.0,
    }])
    # No hay geocoding → stop saltado → status NO_STOPS_GEOCODED
    geocoding = pd.DataFrame(columns=["cliente_id", "lat", "lng", "status",
                                       "query_used", "reason"])
    kpi = kpis.compare(5, canonical=canonical, geocoding=geocoding)
    assert kpi.status == "NO_STOPS_GEOCODED"
    assert kpi.n_paradas == 0


# ---- batch_compare ---------------------------------------------------------

def test_batch_compare_filters_by_min_stops(mini_dataset, tmp_path):
    canonical, geocoding = mini_dataset
    with patch("src.kpis.pd.read_parquet") as fake_read:
        # Simula que pd.read_parquet devuelve canonical o geocoding según path
        fake_read.side_effect = [canonical, geocoding]
        with patch("src.kpis.distance_matrix.get_matrix",
                   side_effect=lambda c: _fake_matrix(c)):
            df = kpis.batch_compare(min_stops=10, persist_csv=None)
    # min_stops=10 pero el transporte sólo tiene 3 paradas → filtrado fuera
    assert df.empty


def test_batch_compare_persists_csv(mini_dataset, tmp_path):
    canonical, geocoding = mini_dataset
    out = tmp_path / "kpi.csv"
    with patch("src.kpis.pd.read_parquet") as fake_read:
        fake_read.side_effect = [canonical, geocoding]
        with patch("src.kpis.distance_matrix.get_matrix",
                   side_effect=lambda c: _fake_matrix(c)):
            df = kpis.batch_compare(min_stops=2, time_limit_s=2,
                                     persist_csv=out)
    assert out.exists()
    assert len(df) == 1
    rt = pd.read_csv(out)
    assert "delta_distancia_pct" in rt.columns


# ---- aggregate_means + plot_summary ---------------------------------------

def test_aggregate_means_handles_empty_df():
    assert kpis.aggregate_means(pd.DataFrame()) == {}


def test_aggregate_means_basic():
    df = pd.DataFrame([
        {"status": "OPTIMAL", "delta_distancia_pct": -10.0,
         "delta_tiempo_pct": -5.0, "delta_movimientos_pct": -33.0,
         "delta_retornables_pp": 25.0},
        {"status": "OPTIMAL", "delta_distancia_pct": -20.0,
         "delta_tiempo_pct": -15.0, "delta_movimientos_pct": -33.0,
         "delta_retornables_pp": 25.0},
        {"status": "OPTIMAL", "delta_distancia_pct": 5.0,    # outlier
         "delta_tiempo_pct": 2.0, "delta_movimientos_pct": -33.0,
         "delta_retornables_pp": 25.0},
    ])
    means = kpis.aggregate_means(df)
    assert means["n_transportes"] == 3
    assert means["delta_distancia_pct_mean"] == pytest.approx(-8.33, abs=0.1)
    assert means["n_outliers_distancia_worse"] == 1


def test_plot_summary_writes_html(tmp_path):
    df = pd.DataFrame([
        {"status": "OPTIMAL", "delta_distancia_pct": -10.0 + i,
         "delta_tiempo_pct": -8.0 + i, "delta_movimientos_pct": -33.0,
         "delta_retornables_pp": 25.0}
        for i in range(20)
    ])
    out = tmp_path / "kpi.html"
    kpis.plot_summary(df, save_to=out)
    assert out.exists()
    assert "plotly" in out.read_text().lower()
