"""Solver VRP base con OR-Tools para los camiones de DDI.

MVP 4: 1 camión, sin ventanas horarias, sin pickup-delivery. Restricción de
capacidad volumétrica (litros) y de peso (kg). El objetivo es minimizar el
tiempo total de la ruta (transit + servicio en cada parada).

**Heurística**:
- ``PATH_CHEAPEST_ARC`` como solución inicial: greedy desde el depot, elige el
  arco más barato a cada paso. Rápido y razonable para ruta única.
- ``GUIDED_LOCAL_SEARCH`` como metaheurística: penaliza arcos caros para
  escapar de óptimos locales. Es la combinación recomendada por la
  documentación oficial de OR-Tools para VRP de tamaño medio (≤ 100 nodos).
- ``time_limit`` por defecto 20 s — suficiente para ≤ 25 paradas en local.

Uso:
    python -m src.vrp_solver --transport 11561535 [--time-limit 20] [--truck 6P]
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from src import config, distance_matrix
from src.exceptions import DammSmartTruckError


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------

@dataclass
class Stop:
    cliente_id: int
    lat: float
    lng: float
    volumen_l: float
    peso_kg: float
    volumen_retornable_l: float = 0.0
    ventana_inicio: int | None = None       # segundos desde medianoche
    ventana_fin: int | None = None
    tiempo_servicio_s: int = 600            # 10 min default
    cliente_nombre: str = ""
    poblacion: str = ""
    entrega_id: int | None = None


@dataclass
class Solution:
    ordered_stops: list[Stop]
    total_time_s: int
    total_distance_m: int
    status: str                              # "OPTIMAL" | "FEASIBLE" | "INFEASIBLE"
    raw_solver_output: dict[str, Any] = field(default_factory=dict)


_OR_STATUS = {
    0: "ROUTING_NOT_SOLVED",
    1: "OPTIMAL",                   # ROUTING_SUCCESS
    2: "ROUTING_PARTIAL_SUCCESS_LOCAL_OPTIMUM_NOT_REACHED",
    3: "ROUTING_FAIL",
    4: "ROUTING_FAIL_TIMEOUT",
    5: "ROUTING_INVALID",
    6: "ROUTING_INFEASIBLE",
}


# ---------------------------------------------------------------------------
# Utilidades de capacidad / unidades
# ---------------------------------------------------------------------------

def _truck_capacity(truck: str) -> tuple[float, float]:
    spec = config.TRUCKS[truck]
    return spec["vol_m3"] * 1000.0, float(spec["peso_max_kg"])


# ---------------------------------------------------------------------------
# Solver principal
# ---------------------------------------------------------------------------

def solve_single_truck(
    stops: list[Stop],
    depot: tuple[float, float],
    truck_capacity_l: float,
    truck_capacity_kg: float,
    time_matrix_s: np.ndarray,
    dist_matrix_m: np.ndarray,
    *,
    max_route_time_s: int = 8 * 3600,
    time_limit_s: int = 20,
    use_time_windows: bool = False,
    use_pickup_delivery: bool = False,
) -> Solution:
    """Resuelve un VRP de 1 camión con capacidad de volumen y peso.

    `time_matrix_s` y `dist_matrix_m` son matrices ``(N+1)x(N+1)`` indexadas como
    ``0=depot`` y ``1..N`` = paradas en el orden de `stops`. Sus valores deben
    venir ya en segundos y metros enteros (se castean a int).

    `use_time_windows` y `use_pickup_delivery` se ignoran en MVP 4 — se
    aceptan en la firma para no romper futuros incrementos.
    """
    del use_time_windows, use_pickup_delivery  # MVP 4: ignorados

    n_stops = len(stops)
    if n_stops == 0:
        return Solution([], 0, 0, "OPTIMAL", {"trivial": "empty"})

    # Sanity check temprano: capacidad excedida → INFEASIBLE inmediato
    total_vol = sum(s.volumen_l for s in stops)
    total_peso = sum(s.peso_kg for s in stops)
    if total_vol > truck_capacity_l or total_peso > truck_capacity_kg:
        logger.warning("Capacidad excedida: vol={:.1f}L vs {:.1f}L | peso={:.1f}kg vs {:.1f}kg",
                       total_vol, truck_capacity_l, total_peso, truck_capacity_kg)
        return Solution(
            ordered_stops=list(stops),
            total_time_s=0, total_distance_m=0, status="INFEASIBLE",
            raw_solver_output={"reason": "capacity_overflow",
                               "total_vol_l": total_vol, "total_peso_kg": total_peso},
        )

    n_nodes = n_stops + 1  # 0 = depot
    if time_matrix_s.shape != (n_nodes, n_nodes) or dist_matrix_m.shape != (n_nodes, n_nodes):
        raise DammSmartTruckError(
            f"Dimensiones inesperadas: time={time_matrix_s.shape}, "
            f"dist={dist_matrix_m.shape}, esperaba ({n_nodes},{n_nodes})"
        )

    # Pre-cast a int para OR-Tools (requiere enteros).
    time_int = np.rint(time_matrix_s).astype(np.int64)
    dist_int = np.rint(dist_matrix_m).astype(np.int64)
    service = [0] + [int(s.tiempo_servicio_s) for s in stops]
    demand_vol = [0] + [int(round(s.volumen_l)) for s in stops]
    demand_kg = [0] + [int(round(s.peso_kg)) for s in stops]
    cap_vol = int(round(truck_capacity_l))
    cap_kg = int(round(truck_capacity_kg))

    manager = pywrapcp.RoutingIndexManager(n_nodes, 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    # ---- coste = tiempo de transit + servicio en destino ----
    def transit_time_cb(from_idx: int, to_idx: int) -> int:
        i = manager.IndexToNode(from_idx)
        j = manager.IndexToNode(to_idx)
        return int(time_int[i, j]) + service[j]

    transit_cb_idx = routing.RegisterTransitCallback(transit_time_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb_idx)

    # Dimensión "Time" sólo para limitar el total de la jornada.
    routing.AddDimension(
        transit_cb_idx,
        0,                    # slack (no necesario sin ventanas)
        int(max_route_time_s),
        True,                 # start at zero
        "Time",
    )

    # ---- capacidad de volumen ----
    def demand_vol_cb(from_idx: int) -> int:
        return demand_vol[manager.IndexToNode(from_idx)]
    vol_cb_idx = routing.RegisterUnaryTransitCallback(demand_vol_cb)
    routing.AddDimensionWithVehicleCapacity(vol_cb_idx, 0, [cap_vol], True, "Volumen")

    # ---- capacidad de peso ----
    def demand_kg_cb(from_idx: int) -> int:
        return demand_kg[manager.IndexToNode(from_idx)]
    kg_cb_idx = routing.RegisterUnaryTransitCallback(demand_kg_cb)
    routing.AddDimensionWithVehicleCapacity(kg_cb_idx, 0, [cap_kg], True, "Peso")

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    params.time_limit.seconds = int(time_limit_s)

    raw_solution = routing.SolveWithParameters(params)
    or_status = routing.status()
    status_label = _OR_STATUS.get(or_status, f"UNKNOWN({or_status})")

    if raw_solution is None:
        return Solution(
            ordered_stops=list(stops),
            total_time_s=0, total_distance_m=0,
            status="INFEASIBLE" if or_status == 6 else status_label,
            raw_solver_output={"or_status": or_status, "label": status_label},
        )

    # ---- Extraer ruta y métricas ----
    ordered: list[Stop] = []
    total_time = 0
    total_dist = 0
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node != 0:
            ordered.append(stops[node - 1])
        next_index = raw_solution.Value(routing.NextVar(index))
        i = manager.IndexToNode(index)
        j = manager.IndexToNode(next_index)
        total_time += int(time_int[i, j]) + service[j]
        total_dist += int(dist_int[i, j])
        index = next_index

    return Solution(
        ordered_stops=ordered,
        total_time_s=int(total_time),
        total_distance_m=int(total_dist),
        status="OPTIMAL" if status_label in ("OPTIMAL", "ROUTING_SUCCESS") else status_label,
        raw_solver_output={"or_status": or_status, "objective": raw_solution.ObjectiveValue()},
    )


# ---------------------------------------------------------------------------
# Métricas baseline (orden real del dataset)
# ---------------------------------------------------------------------------

def baseline_metrics(stops: list[Stop], time_matrix_s: np.ndarray,
                     dist_matrix_m: np.ndarray) -> tuple[int, int]:
    """Tiempo total y distancia total recorriendo `stops` en orden dado."""
    if not stops:
        return 0, 0
    time_int = np.rint(time_matrix_s).astype(np.int64)
    dist_int = np.rint(dist_matrix_m).astype(np.int64)
    total_t = 0
    total_d = 0
    prev = 0
    for k, _ in enumerate(stops, 1):
        total_t += int(time_int[prev, k]) + int(stops[k - 1].tiempo_servicio_s)
        total_d += int(dist_int[prev, k])
        prev = k
    # vuelta al depot
    total_t += int(time_int[prev, 0])
    total_d += int(dist_int[prev, 0])
    return total_t, total_d


# ---------------------------------------------------------------------------
# Construcción de stops desde el dataset canonical
# ---------------------------------------------------------------------------

def build_stops_from_transporte(
    transporte_id: int,
    canonical: pd.DataFrame,
    geocoding: pd.DataFrame,
) -> list[Stop]:
    """Devuelve la lista de Stop para un transporte, ordenada por `entrega_id`.

    Ignora silenciosamente clientes sin geocoding válido (loggea cuántos).
    """
    sub = canonical[canonical["transporte"] == transporte_id].sort_values("entrega_id")
    if sub.empty:
        raise DammSmartTruckError(f"Transporte {transporte_id} no existe en canonical")

    geo_ok = geocoding[geocoding["status"].astype(str).str.startswith("ok")]
    geo_idx = geo_ok.set_index("cliente_id")[["lat", "lng"]]

    stops: list[Stop] = []
    skipped = 0
    for r in sub.itertuples(index=False):
        if r.cliente_id not in geo_idx.index:
            skipped += 1
            continue
        lat, lng = geo_idx.loc[r.cliente_id, ["lat", "lng"]]
        # Tiempo de servicio: 10 min base + 2 min por palet equivalente
        # (1 palet ~= 2400 L). Mín 8 min, máx 60 min.
        palet_eq = max(0.0, float(r.volumen_total_l) / 2400.0)
        t_serv = max(480, min(3600, int(600 + palet_eq * 120)))
        stops.append(Stop(
            cliente_id=int(r.cliente_id),
            lat=float(lat), lng=float(lng),
            volumen_l=float(r.volumen_total_l),
            peso_kg=float(r.peso_total_kg),
            volumen_retornable_l=float(r.volumen_retornable_l),
            tiempo_servicio_s=t_serv,
            cliente_nombre=str(r.cliente_nombre),
            poblacion=str(r.poblacion),
            entrega_id=int(r.entrega_id),
        ))
    if skipped:
        logger.warning("Transporte {}: {} clientes sin geocoding (saltados)",
                       transporte_id, skipped)
    return stops


# ---------------------------------------------------------------------------
# Pipeline end-to-end (CLI)
# ---------------------------------------------------------------------------

def _format_hms(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:01d}h{m:02d}m{s:02d}s"


def run_for_transporte(
    transporte_id: int,
    truck: str = "6P",
    time_limit_s: int = 20,
) -> dict:
    canonical = pd.read_parquet(config.CANONICAL_PARQUET)
    geo = pd.read_parquet(config.GEOCODING_PARQUET)

    stops = build_stops_from_transporte(transporte_id, canonical, geo)
    if not stops:
        raise DammSmartTruckError(f"Transporte {transporte_id} sin paradas geocodificadas")

    coords = [(config.DEPOT_LAT, config.DEPOT_LNG)] + [(s.lat, s.lng) for s in stops]
    time_mat, dist_mat = distance_matrix.get_matrix(coords)

    cap_l, cap_kg = _truck_capacity(truck)
    base_time, base_dist = baseline_metrics(stops, time_mat, dist_mat)
    sol = solve_single_truck(
        stops, (config.DEPOT_LAT, config.DEPOT_LNG),
        cap_l, cap_kg, time_mat, dist_mat,
        time_limit_s=time_limit_s,
    )

    delta_t = sol.total_time_s - base_time
    delta_d = sol.total_distance_m - base_dist
    pct_t = 100 * delta_t / base_time if base_time else 0.0
    pct_d = 100 * delta_d / base_dist if base_dist else 0.0

    print(f"\n=== Transporte {transporte_id} | camión {truck} | {len(stops)} paradas ===")
    print(f"Status solver: {sol.status}")
    print(f"\nBaseline (orden real, sort by entrega_id):")
    print(f"  tiempo:    {_format_hms(base_time)}  ({base_time} s)")
    print(f"  distancia: {base_dist/1000:8.2f} km")
    print(f"\nOptimizado (OR-Tools, GLS):")
    print(f"  tiempo:    {_format_hms(sol.total_time_s)}  ({sol.total_time_s} s)")
    print(f"  distancia: {sol.total_distance_m/1000:8.2f} km")
    print(f"\nΔ vs baseline:")
    print(f"  tiempo:    {delta_t:+d} s   ({pct_t:+.2f}%)")
    print(f"  distancia: {delta_d:+d} m   ({pct_d:+.2f}%)")
    print(f"\nOrden propuesto (cliente_id | nombre | población | volumen_l):")
    for k, s in enumerate(sol.ordered_stops, 1):
        print(f"  {k:2d}. {s.cliente_id} | {s.cliente_nombre[:30]:30s} | "
              f"{s.poblacion[:18]:18s} | {s.volumen_l:7.1f} L")

    return {
        "transporte": transporte_id,
        "n_stops": len(stops),
        "baseline_time_s": base_time, "baseline_dist_m": base_dist,
        "opt_time_s": sol.total_time_s, "opt_dist_m": sol.total_distance_m,
        "delta_time_pct": pct_t, "delta_dist_pct": pct_d,
        "status": sol.status,
    }


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", type=int, required=True,
                        help="ID de transporte (e.g. 11561535)")
    parser.add_argument("--truck", default="6P", choices=list(config.TRUCKS.keys()))
    parser.add_argument("--time-limit", type=int, default=20,
                        help="Segundos de tiempo límite para metaheurística")
    args = parser.parse_args()
    run_for_transporte(args.transport, truck=args.truck, time_limit_s=args.time_limit)


if __name__ == "__main__":
    _main()
