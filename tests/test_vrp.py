"""Tests del solver VRP base (MVP 4)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import config, vrp_solver as vrp
from src.vrp_solver import Stop


# ---- helpers ----------------------------------------------------------------

def _stop(cid: int, vol: float = 100.0, peso: float = 50.0,
          tserv: int = 600) -> Stop:
    return Stop(cliente_id=cid, lat=0.0, lng=0.0, volumen_l=vol,
                peso_kg=peso, tiempo_servicio_s=tserv)


def _identity_matrices(n: int, value: int = 60) -> tuple[np.ndarray, np.ndarray]:
    """Tiempo `value` segundos y distancia `value*10` m entre cada par i≠j."""
    t = np.full((n, n), value, dtype=np.int64)
    d = np.full((n, n), value * 10, dtype=np.int64)
    np.fill_diagonal(t, 0)
    np.fill_diagonal(d, 0)
    return t, d


# ---- caso 0 paradas ---------------------------------------------------------

def test_solve_empty_returns_trivial_solution():
    sol = vrp.solve_single_truck(
        stops=[], depot=(0, 0), truck_capacity_l=10_000, truck_capacity_kg=5_000,
        time_matrix_s=np.zeros((1, 1)), dist_matrix_m=np.zeros((1, 1)),
    )
    assert sol.ordered_stops == []
    assert sol.total_time_s == 0
    assert sol.total_distance_m == 0
    assert sol.status == "OPTIMAL"


# ---- caso 2 paradas (trivial: A→B o B→A, ambos válidos) ---------------------

def test_solve_two_stops_returns_valid_solution():
    stops = [_stop(1), _stop(2)]
    t, d = _identity_matrices(3, value=60)
    sol = vrp.solve_single_truck(
        stops, (0, 0), 10_000, 5_000, t, d,
    )
    assert sol.status in ("OPTIMAL", "FEASIBLE", "ROUTING_SUCCESS")
    assert len(sol.ordered_stops) == 2
    assert {s.cliente_id for s in sol.ordered_stops} == {1, 2}


# ---- caso 5 paradas con óptimo conocido -------------------------------------

def test_solve_five_stops_known_optimum():
    """Coords colineales: depot=0, A=1, B=2, C=3, D=4, E=5 sobre el eje X.
    El óptimo evidente es 0→1→2→3→4→5→0 (ida y vuelta lineal).
    """
    n_nodes = 6
    t = np.zeros((n_nodes, n_nodes), dtype=np.int64)
    d = np.zeros((n_nodes, n_nodes), dtype=np.int64)
    for i in range(n_nodes):
        for j in range(n_nodes):
            t[i, j] = abs(i - j) * 60       # 1 minuto por unidad
            d[i, j] = abs(i - j) * 1000     # 1 km por unidad

    stops = [_stop(i, vol=10, peso=5, tserv=0) for i in range(1, 6)]
    sol = vrp.solve_single_truck(
        stops, (0, 0), 10_000, 5_000, t, d,
    )
    ids = [s.cliente_id for s in sol.ordered_stops]
    assert ids == [1, 2, 3, 4, 5] or ids == [5, 4, 3, 2, 1]
    # Distancia óptima: ida (5km) + vuelta (5km) = 10 km
    assert sol.total_distance_m == 10_000
    # Tiempo: 5 + 5 = 10 min = 600 s (servicio 0)
    assert sol.total_time_s == 600


# ---- capacidad excedida -----------------------------------------------------

def test_solve_capacity_overflow_returns_infeasible():
    stops = [_stop(1, vol=8000), _stop(2, vol=8000)]   # 16k > cap 10k
    t, d = _identity_matrices(3)
    sol = vrp.solve_single_truck(
        stops, (0, 0), 10_000, 5_000, t, d,
    )
    assert sol.status == "INFEASIBLE"
    assert sol.raw_solver_output["reason"] == "capacity_overflow"


def test_solve_peso_overflow_returns_infeasible():
    stops = [_stop(1, vol=100, peso=4000), _stop(2, vol=100, peso=4000)]
    t, d = _identity_matrices(3)
    sol = vrp.solve_single_truck(
        stops, (0, 0), 10_000, 5_000, t, d,
    )
    assert sol.status == "INFEASIBLE"


# ---- baseline_metrics -------------------------------------------------------

def test_baseline_metrics_simple():
    """Recorrido 0→1→2→3→0 con tiempos uniformes."""
    stops = [_stop(1, tserv=0), _stop(2, tserv=0), _stop(3, tserv=0)]
    t, d = _identity_matrices(4, value=120)  # 2 min / 1.2 km por arco
    bt, bd = vrp.baseline_metrics(stops, t, d)
    # 4 arcos: 0→1, 1→2, 2→3, 3→0
    assert bt == 4 * 120
    assert bd == 4 * 1200


def test_baseline_metrics_includes_service_time():
    stops = [_stop(1, tserv=300), _stop(2, tserv=300)]
    t, d = _identity_matrices(3, value=60)
    bt, _ = vrp.baseline_metrics(stops, t, d)
    # 3 arcos × 60 s + 2 paradas × 300 s servicio
    assert bt == 3 * 60 + 2 * 300


# ---- solver vs baseline: optimizado nunca peor que real (en este caso) ------

def test_solve_improves_or_matches_baseline_on_misordered_input():
    """Si paso paradas en orden subóptimo, OR-Tools debe re-ordenarlas y
    el tiempo total no debe ser peor."""
    n_nodes = 6
    t = np.zeros((n_nodes, n_nodes), dtype=np.int64)
    d = np.zeros((n_nodes, n_nodes), dtype=np.int64)
    for i in range(n_nodes):
        for j in range(n_nodes):
            t[i, j] = abs(i - j) * 60
            d[i, j] = abs(i - j) * 1000

    # Paso las paradas desordenadas: 3, 1, 5, 2, 4
    misordered = [_stop(3, tserv=0), _stop(1, tserv=0), _stop(5, tserv=0),
                  _stop(2, tserv=0), _stop(4, tserv=0)]
    # Pero el time_matrix sigue siendo el original donde nodo k = parada k.
    # Re-mapeo manual: time_matrix indexa por la posición en `stops`.
    n = len(misordered) + 1
    t2 = np.zeros((n, n), dtype=np.int64)
    d2 = np.zeros((n, n), dtype=np.int64)
    pos = [0] + [s.cliente_id for s in misordered]   # 0,3,1,5,2,4
    for i in range(n):
        for j in range(n):
            t2[i, j] = abs(pos[i] - pos[j]) * 60
            d2[i, j] = abs(pos[i] - pos[j]) * 1000

    base_t, base_d = vrp.baseline_metrics(misordered, t2, d2)
    sol = vrp.solve_single_truck(misordered, (0, 0), 10_000, 5_000, t2, d2)
    assert sol.total_distance_m <= base_d
    assert sol.total_time_s <= base_t


# ---- dimensiones inconsistentes --------------------------------------------

def test_solve_raises_on_dim_mismatch():
    from src.exceptions import DammSmartTruckError
    stops = [_stop(1), _stop(2)]
    t = np.zeros((2, 2), dtype=np.int64)   # debería ser 3x3
    d = np.zeros((2, 2), dtype=np.int64)
    with pytest.raises(DammSmartTruckError):
        vrp.solve_single_truck(stops, (0, 0), 10_000, 5_000, t, d)


# ---- build_stops_from_transporte -------------------------------------------

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
        # cliente 200 NO está en geocoding → debe saltarse
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


# ---- MVP 5 — VRPTW ---------------------------------------------------------

def _stop_w(cid: int, ini: int | None, fin: int | None, vol: float = 100.0,
            tserv: int = 0) -> Stop:
    return Stop(cliente_id=cid, lat=0.0, lng=0.0, volumen_l=vol, peso_kg=10.0,
                tiempo_servicio_s=tserv, ventana_inicio=ini, ventana_fin=fin)


def test_solve_with_time_windows_forces_order():
    """3 paradas equidistantes, pero las ventanas obligan a visitar B antes que A."""
    n = 4
    t = np.zeros((n, n), dtype=np.int64)
    d = np.zeros((n, n), dtype=np.int64)
    for i in range(n):
        for j in range(n):
            t[i, j] = 1800 if i != j else 0   # 30 min entre cada par
            d[i, j] = 5000 if i != j else 0

    # Depot abre 08:00. B sólo abre 08:00–09:00, A sólo 10:00–11:00, C 09:00–12:00.
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
    assert ids == [102, 103, 101], f"orden inesperado: {ids}"
    arrivals = sol.raw_solver_output.get("arrivals_s", [])
    # B llega antes de las 09:00, C antes de las 12:00, A antes de las 11:00
    assert arrivals[0] <= 9 * HH
    assert arrivals[1] <= 12 * HH
    assert arrivals[2] <= 11 * HH


def test_solve_with_time_windows_unreachable_returns_infeasible():
    """Una parada con ventana cerrada antes de que el camión pueda llegar."""
    n = 3
    t = np.full((n, n), 7200, dtype=np.int64)   # 2 h entre cada par
    d = np.full((n, n), 50_000, dtype=np.int64)
    np.fill_diagonal(t, 0); np.fill_diagonal(d, 0)

    HH = 3600
    # Imposible: depot abre 08:00, viaje al cliente 2 h, ventana cierra a 09:00.
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
    """Si pasas ventanas pero use_time_windows=False, el solver no las aplica."""
    n = 4
    t = np.full((n, n), 1800, dtype=np.int64); np.fill_diagonal(t, 0)
    d = np.full((n, n), 5000, dtype=np.int64); np.fill_diagonal(d, 0)
    HH = 3600
    # Ventanas que en VRPTW=ON forzarían el orden 102 → 103 → 101,
    # pero al estar OFF, el solver es libre.
    stops = [_stop_w(101, 10*HH, 11*HH), _stop_w(102, 8*HH, 9*HH),
             _stop_w(103, 9*HH, 12*HH)]
    sol = vrp.solve_single_truck(
        stops, (0, 0), 10_000, 5_000, t, d,
        use_time_windows=False,
    )
    assert sol.status in ("OPTIMAL", "FEASIBLE", "ROUTING_SUCCESS")
    assert sol.raw_solver_output.get("arrivals_s") is None  # no se computan


# ---- attach_time_windows ----------------------------------------------------

def test_attach_time_windows_populates_known_clients(monkeypatch):
    from datetime import date

    canonical = pd.DataFrame([
        {"cliente_id": 122348, "cliente_nombre": "BAR JOAN PRIM"},
        {"cliente_id": 999999, "cliente_nombre": "CLIENTE_X"},
    ])
    horarios = pd.DataFrame([
        {"deudor": 122348, "nombre_norm": "BARJOANPRIM", "dia_semana": 1,
         "turno": 1, "inicio_s": 10*3600, "fin_s": 11*3600, "cierre_total": False},
    ])
    # Lunes 2026-02-02
    fecha = date(2026, 2, 2)
    assert fecha.weekday() + 1 == 1

    stops = [
        Stop(cliente_id=122348, lat=0, lng=0, volumen_l=10, peso_kg=5),
        Stop(cliente_id=999999, lat=0, lng=0, volumen_l=10, peso_kg=5),
    ]
    out = vrp.attach_time_windows(stops, fecha, canonical=canonical, horarios=horarios)
    assert out[0].ventana_inicio == 10 * 3600
    assert out[0].ventana_fin == 11 * 3600
    assert out[1].ventana_inicio is None  # cliente sin horario


def test_attach_time_windows_skips_closed_day():
    """00:00 → 00:00 codifica CERRADO ese día — no debe asignarse ventana."""
    from datetime import date
    canonical = pd.DataFrame([{"cliente_id": 122348, "cliente_nombre": "X"}])
    horarios = pd.DataFrame([
        {"deudor": 122348, "nombre_norm": "X", "dia_semana": 2,
         "turno": 1, "inicio_s": 0, "fin_s": 0, "cierre_total": False},
    ])
    # Martes
    fecha = date(2026, 2, 3)
    stops = [Stop(cliente_id=122348, lat=0, lng=0, volumen_l=10, peso_kg=5)]
    out = vrp.attach_time_windows(stops, fecha, canonical=canonical, horarios=horarios)
    assert out[0].ventana_inicio is None
    assert out[0].ventana_fin is None
