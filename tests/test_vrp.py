"""Tests del solver VRP (MVPs 4, 5, 7)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import config, vrp_solver as vrp
from src.vrp_solver import Stop


def _stop(cid: int, vol: float = 100.0, peso: float = 50.0,
          tserv: int = 600) -> Stop:
    return Stop(cliente_id=cid, lat=0.0, lng=0.0, volumen_l=vol,
                peso_kg=peso, tiempo_servicio_s=tserv)


def _identity_matrices(n: int, value: int = 60) -> tuple[np.ndarray, np.ndarray]:
    t = np.full((n, n), value, dtype=np.int64)
    d = np.full((n, n), value * 10, dtype=np.int64)
    np.fill_diagonal(t, 0)
    np.fill_diagonal(d, 0)
    return t, d


def test_solve_empty_returns_trivial_solution():
    sol = vrp.solve_single_truck(
        stops=[], depot=(0, 0), truck_capacity_l=10_000, truck_capacity_kg=5_000,
        time_matrix_s=np.zeros((1, 1)), dist_matrix_m=np.zeros((1, 1)),
    )
    assert sol.ordered_stops == []
    assert sol.total_time_s == 0
    assert sol.total_distance_m == 0
    assert sol.status == "OPTIMAL"


def test_solve_two_stops_returns_valid_solution():
    stops = [_stop(1), _stop(2)]
    t, d = _identity_matrices(3, value=60)
    sol = vrp.solve_single_truck(stops, (0, 0), 10_000, 5_000, t, d)
    assert sol.status in ("OPTIMAL", "FEASIBLE", "ROUTING_SUCCESS")
    assert len(sol.ordered_stops) == 2
    assert {s.cliente_id for s in sol.ordered_stops} == {1, 2}


def test_solve_five_stops_known_optimum():
    n_nodes = 6
    t = np.zeros((n_nodes, n_nodes), dtype=np.int64)
    d = np.zeros((n_nodes, n_nodes), dtype=np.int64)
    for i in range(n_nodes):
        for j in range(n_nodes):
            t[i, j] = abs(i - j) * 60
            d[i, j] = abs(i - j) * 1000

    stops = [_stop(i, vol=10, peso=5, tserv=0) for i in range(1, 6)]
    sol = vrp.solve_single_truck(stops, (0, 0), 10_000, 5_000, t, d)
    ids = [s.cliente_id for s in sol.ordered_stops]
    assert ids == [1, 2, 3, 4, 5] or ids == [5, 4, 3, 2, 1]
    assert sol.total_distance_m == 10_000
    assert sol.total_time_s == 600


def test_solve_capacity_overflow_returns_infeasible():
    stops = [_stop(1, vol=8000), _stop(2, vol=8000)]
    t, d = _identity_matrices(3)
    sol = vrp.solve_single_truck(stops, (0, 0), 10_000, 5_000, t, d)
    assert sol.status == "INFEASIBLE"
    assert sol.raw_solver_output["reason"] == "capacity_overflow"


def test_solve_peso_overflow_returns_infeasible():
    stops = [_stop(1, vol=100, peso=4000), _stop(2, vol=100, peso=4000)]
    t, d = _identity_matrices(3)
    sol = vrp.solve_single_truck(stops, (0, 0), 10_000, 5_000, t, d)
    assert sol.status == "INFEASIBLE"


def test_baseline_metrics_simple():
    stops = [_stop(1, tserv=0), _stop(2, tserv=0), _stop(3, tserv=0)]
    t, d = _identity_matrices(4, value=120)
    bt, bd = vrp.baseline_metrics(stops, t, d)
    assert bt == 4 * 120
    assert bd == 4 * 1200


def test_baseline_metrics_includes_service_time():
    stops = [_stop(1, tserv=300), _stop(2, tserv=300)]
    t, d = _identity_matrices(3, value=60)
    bt, _ = vrp.baseline_metrics(stops, t, d)
    assert bt == 3 * 60 + 2 * 300


def test_solve_improves_or_matches_baseline_on_misordered_input():
    n_nodes = 6
    t = np.zeros((n_nodes, n_nodes), dtype=np.int64)
    d = np.zeros((n_nodes, n_nodes), dtype=np.int64)
    for i in range(n_nodes):
        for j in range(n_nodes):
            t[i, j] = abs(i - j) * 60
            d[i, j] = abs(i - j) * 1000

    misordered = [_stop(3, tserv=0), _stop(1, tserv=0), _stop(5, tserv=0),
                  _stop(2, tserv=0), _stop(4, tserv=0)]
    n = len(misordered) + 1
    t2 = np.zeros((n, n), dtype=np.int64)
    d2 = np.zeros((n, n), dtype=np.int64)
    pos = [0] + [s.cliente_id for s in misordered]
    for i in range(n):
        for j in range(n):
            t2[i, j] = abs(pos[i] - pos[j]) * 60
            d2[i, j] = abs(pos[i] - pos[j]) * 1000

    base_t, base_d = vrp.baseline_metrics(misordered, t2, d2)
    sol = vrp.solve_single_truck(misordered, (0, 0), 10_000, 5_000, t2, d2)
    assert sol.total_distance_m <= base_d
    assert sol.total_time_s <= base_t


def test_solve_raises_on_dim_mismatch():
    from src.exceptions import DammSmartTruckError
    stops = [_stop(1), _stop(2)]
    t = np.zeros((2, 2), dtype=np.int64)
    d = np.zeros((2, 2), dtype=np.int64)
    with pytest.raises(DammSmartTruckError):
        vrp.solve_single_truck(stops, (0, 0), 10_000, 5_000, t, d)


def test_build_stops_skips_clients_without_geocoding():
    canonical = pd.DataFrame([
        {"transporte": 999, "entrega_id": 1, "cliente_id": 100,
         "cliente_nombre": "A", "poblacion": "X", "volumen_total_l": 50.0,
         "peso_total_kg": 20.0, "volumen_retornable_l": 0.0},
        {"transporte": 999, "entrega_id": 2, "cliente_id": 200,
         "cliente_nombre": "B", "poblacion": "Y", "volumen_total_l": 30.0,
         "peso_total_kg": 10.0, "volumen_retornable_l": 0.0},
    ])
    geocoding = pd.DataFrame([
        {"cliente_id": 100, "lat": 41.5, "lng": 2.2, "status": "ok",
         "query_used": "", "reason": ""},
    ])
    stops = vrp.build_stops_from_transporte(999, canonical, geocoding)
    assert len(stops) == 1
    assert stops[0].cliente_id == 100
    assert stops[0].lat == pytest.approx(41.5)


def test_build_stops_orders_by_entrega_id():
    canonical = pd.DataFrame([
        {"transporte": 999, "entrega_id": 50, "cliente_id": 200,
         "cliente_nombre": "B", "poblacion": "Y", "volumen_total_l": 30.0,
         "peso_total_kg": 10.0, "volumen_retornable_l": 0.0},
        {"transporte": 999, "entrega_id": 10, "cliente_id": 100,
         "cliente_nombre": "A", "poblacion": "X", "volumen_total_l": 50.0,
         "peso_total_kg": 20.0, "volumen_retornable_l": 0.0},
    ])
    geocoding = pd.DataFrame([
        {"cliente_id": 100, "lat": 41.5, "lng": 2.2, "status": "ok",
         "query_used": "", "reason": ""},
        {"cliente_id": 200, "lat": 41.6, "lng": 2.3, "status": "ok",
         "query_used": "", "reason": ""},
    ])
    stops = vrp.build_stops_from_transporte(999, canonical, geocoding)
    assert [s.cliente_id for s in stops] == [100, 200]


def test_build_stops_raises_for_unknown_transporte():
    from src.exceptions import DammSmartTruckError
    canonical = pd.DataFrame(columns=[
        "transporte", "entrega_id", "cliente_id", "cliente_nombre", "poblacion",
        "volumen_total_l", "peso_total_kg", "volumen_retornable_l",
    ])
    geocoding = pd.DataFrame(columns=["cliente_id", "lat", "lng", "status",
                                       "query_used", "reason"])
    with pytest.raises(DammSmartTruckError):
        vrp.build_stops_from_transporte(123, canonical, geocoding)


# ---- MVP 5 -- VRPTW --------------------------------------------------------

def _stop_w(cid: int, ini, fin, vol: float = 100.0, tserv: int = 0) -> Stop:
    return Stop(cliente_id=cid, lat=0.0, lng=0.0, volumen_l=vol, peso_kg=10.0,
                tiempo_servicio_s=tserv, ventana_inicio=ini, ventana_fin=fin)


def test_solve_with_time_windows_forces_order():
    n = 4
    t = np.zeros((n, n), dtype=np.int64)
    d = np.zeros((n, n), dtype=np.int64)
    for i in range(n):
        for j in range(n):
            t[i, j] = 1800 if i != j else 0
            d[i, j] = 5000 if i != j else 0

    HH = 3600
    A = _stop_w(101, 10*HH, 11*HH)
    B = _stop_w(102,  8*HH,  9*HH)
    C = _stop_w(103,  9*HH, 12*HH)
    sol = vrp.solve_single_truck(
        [A, B, C], (0, 0), 10_000, 5_000, t, d,
        use_time_windows=True,
        depot_open_s=8 * HH, depot_close_s=14 * HH,
        time_limit_s=10,
    )
    assert sol.status in ("OPTIMAL", "FEASIBLE", "ROUTING_SUCCESS")
    ids = [s.cliente_id for s in sol.ordered_stops]
    assert ids == [102, 103, 101]
    arrivals = sol.raw_solver_output.get("arrivals_s", [])
    assert arrivals[0] <= 9 * HH
    assert arrivals[1] <= 12 * HH
    assert arrivals[2] <= 11 * HH


def test_solve_with_time_windows_unreachable_returns_infeasible():
    n = 3
    t = np.full((n, n), 7200, dtype=np.int64)
    d = np.full((n, n), 50_000, dtype=np.int64)
    np.fill_diagonal(t, 0); np.fill_diagonal(d, 0)
    HH = 3600
    far_stop = _stop_w(999, 8 * HH, 9 * HH)
    other = _stop_w(998, 10 * HH, 14 * HH)
    sol = vrp.solve_single_truck(
        [far_stop, other], (0, 0), 10_000, 5_000, t, d,
        use_time_windows=True,
        depot_open_s=8 * HH, depot_close_s=18 * HH,
    )
    assert sol.status == "INFEASIBLE"
    assert sol.raw_solver_output["reason"] == "time_window_unreachable"
    bad_ids = [u["cliente_id"] for u in sol.raw_solver_output["stops"]]
    assert 999 in bad_ids


def test_solve_without_time_windows_ignores_them():
    n = 4
    t = np.full((n, n), 1800, dtype=np.int64); np.fill_diagonal(t, 0)
    d = np.full((n, n), 5000, dtype=np.int64); np.fill_diagonal(d, 0)
    HH = 3600
    stops = [_stop_w(101, 10*HH, 11*HH), _stop_w(102, 8*HH, 9*HH),
             _stop_w(103, 9*HH, 12*HH)]
    sol = vrp.solve_single_truck(
        stops, (0, 0), 10_000, 5_000, t, d,
        use_time_windows=False,
    )
    assert sol.status in ("OPTIMAL", "FEASIBLE", "ROUTING_SUCCESS")
    assert sol.raw_solver_output.get("arrivals_s") is None


def test_attach_time_windows_populates_known_clients():
    from datetime import date
    canonical = pd.DataFrame([
        {"cliente_id": 122348, "cliente_nombre": "BAR JOAN PRIM"},
        {"cliente_id": 999999, "cliente_nombre": "CLIENTE_X"},
    ])
    horarios = pd.DataFrame([
        {"deudor": 122348, "nombre_norm": "BARJOANPRIM", "dia_semana": 1,
         "turno": 1, "inicio_s": 10*3600, "fin_s": 11*3600, "cierre_total": False},
    ])
    fecha = date(2026, 2, 2)
    assert fecha.weekday() + 1 == 1
    stops = [
        Stop(cliente_id=122348, lat=0, lng=0, volumen_l=10, peso_kg=5),
        Stop(cliente_id=999999, lat=0, lng=0, volumen_l=10, peso_kg=5),
    ]
    out = vrp.attach_time_windows(stops, fecha, canonical=canonical, horarios=horarios)
    assert out[0].ventana_inicio == 10 * 3600
    assert out[0].ventana_fin == 11 * 3600
    assert out[1].ventana_inicio is None


def test_attach_time_windows_skips_closed_day():
    from datetime import date
    canonical = pd.DataFrame([{"cliente_id": 122348, "cliente_nombre": "X"}])
    horarios = pd.DataFrame([
        {"deudor": 122348, "nombre_norm": "X", "dia_semana": 2,
         "turno": 1, "inicio_s": 0, "fin_s": 0, "cierre_total": False},
    ])
    fecha = date(2026, 2, 3)
    stops = [Stop(cliente_id=122348, lat=0, lng=0, volumen_l=10, peso_kg=5)]
    out = vrp.attach_time_windows(stops, fecha, canonical=canonical, horarios=horarios)
    assert out[0].ventana_inicio is None
    assert out[0].ventana_fin is None


# ---- MVP 7 -- Logistica inversa --------------------------------------------

def _stop_r(cid: int, vol: float = 100.0, ret: float = 0.0,
            peso: float = 50.0, tserv: int = 0) -> Stop:
    return Stop(cliente_id=cid, lat=0.0, lng=0.0, volumen_l=vol,
                peso_kg=peso, volumen_retornable_l=ret, tiempo_servicio_s=tserv)


def test_compute_carga_viva_profile_simple():
    stops = [_stop_r(1, vol=100, ret=60),
             _stop_r(2, vol=100, ret=60),
             _stop_r(3, vol=100, ret=60)]
    perfil, pico, pico_idx, total_ret = vrp.compute_carga_viva_profile(stops)
    assert perfil == [300.0, 260.0, 220.0, 180.0]
    assert pico == 300.0
    assert pico_idx == 0
    assert total_ret == 180.0


def test_compute_carga_viva_profile_pico_intermedio():
    stops = [
        _stop_r(1, vol=100, ret=10),
        _stop_r(2, vol=50,  ret=300),
        _stop_r(3, vol=150, ret=10),
    ]
    perfil, pico, pico_idx, _ = vrp.compute_carga_viva_profile(stops)
    assert perfil[0] == 300.0
    assert perfil[2] == pytest.approx(460.0)
    assert pico == pytest.approx(460.0)
    assert pico_idx == 2


def test_compute_carga_viva_profile_empty():
    perfil, pico, pico_idx, total_ret = vrp.compute_carga_viva_profile([])
    assert perfil == [0.0]
    assert pico == 0.0 and pico_idx == 0 and total_ret == 0.0


def test_solve_with_pickup_delivery_populates_profile():
    stops = [_stop_r(1, vol=200, ret=120),
             _stop_r(2, vol=200, ret=120),
             _stop_r(3, vol=200, ret=120)]
    t, d = _identity_matrices(4, value=60)
    sol = vrp.solve_single_truck(
        stops, (0, 0), 10_000, 5_000, t, d,
        use_pickup_delivery=True,
    )
    assert sol.status in ("OPTIMAL", "FEASIBLE", "ROUTING_SUCCESS")
    assert len(sol.perfil_carga_l) == 4
    assert sol.perfil_carga_l[0] == pytest.approx(600.0)
    assert sol.carga_viva_max_l == pytest.approx(600.0)
    assert sol.pico_parada_idx == 0
    assert sol.total_retornable_l == pytest.approx(360.0)


def test_solve_with_pickup_delivery_total_returns_overflow():
    stops = [_stop_r(1, vol=100, ret=8000),
             _stop_r(2, vol=100, ret=8000)]
    t, d = _identity_matrices(3)
    sol = vrp.solve_single_truck(
        stops, (0, 0), 10_000, 5_000, t, d,
        use_pickup_delivery=True,
    )
    assert sol.status == "INFEASIBLE"
    assert sol.raw_solver_output["reason"] == "returns_total_overflow"
    assert sol.total_retornable_l == pytest.approx(16_000.0)


def test_solve_pickup_delivery_off_leaves_profile_empty():
    stops = [_stop_r(1, vol=200, ret=120), _stop_r(2, vol=200, ret=120)]
    t, d = _identity_matrices(3, value=60)
    sol = vrp.solve_single_truck(
        stops, (0, 0), 10_000, 5_000, t, d,
        use_pickup_delivery=False,
    )
    assert sol.status in ("OPTIMAL", "FEASIBLE", "ROUTING_SUCCESS")
    assert sol.perfil_carga_l == []
    assert sol.carga_viva_max_l == 0.0
    assert sol.total_retornable_l == 0.0


def test_solve_with_pickup_delivery_pico_overflow_returns_infeasible():
    stops = [
        _stop_r(1, vol=200, ret=10),
        _stop_r(2, vol=50,  ret=380),
        _stop_r(3, vol=150, ret=10),
    ]
    t, d = _identity_matrices(4, value=60)
    sol = vrp.solve_single_truck(
        stops, (0, 0), truck_capacity_l=500, truck_capacity_kg=5_000,
        time_matrix_s=t, dist_matrix_m=d,
        use_pickup_delivery=True,
    )
    assert sol.status == "INFEASIBLE"
    assert sol.raw_solver_output["reason"] == "returns_pico_overflow"
    assert sol.carga_viva_max_l == pytest.approx(540.0)
    assert sol.pico_parada_idx == 2
