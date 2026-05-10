"""Solver VRP con OR-Tools para los camiones de DDI.

- MVP 4: 1 camion, capacidad de volumen + peso, minimizar tiempo total.
- MVP 5: ventanas horarias (`use_time_windows=True`) con la convencion de
  *service-at-source* (CumulVar(j) = tiempo de **llegada** a j).
- MVP 7: logistica inversa (`use_pickup_delivery=True`) -- modelo simple. En
  cada parada el camion descarga `vol_l` y recoge `volumen_retornable_l`. Se
  anade una segunda dimension de capacidad para limitar el total de retornos
  a la capacidad del camion, y tras resolver se calcula el perfil de carga
  viva y se reporta el pico (INFEASIBLE si excede capacidad).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from src import config, distance_matrix, horarios as horarios_mod
from src.exceptions import DammSmartTruckError
from src.insights import analyze_route, format_insights_for_terminal
from src.loading_visualization import (
    visualize_loading_plan,
    format_loading_plan_for_terminal,
    export_loading_plan_html,
)


@dataclass
class Stop:
    cliente_id: int
    lat: float
    lng: float
    volumen_l: float
    peso_kg: float
    volumen_retornable_l: float = 0.0
    ventana_inicio: int | None = None
    ventana_fin: int | None = None
    tiempo_servicio_s: int = 600
    cliente_nombre: str = ""
    poblacion: str = ""
    entrega_id: int | None = None
    materiales_json: str = "[]"
    n_materiales: int = 0
    n_lineas: int = 0
    pct_retornable: float = 0.0


@dataclass
class Solution:
    ordered_stops: list[Stop]
    total_time_s: int
    total_distance_m: int
    status: str
    raw_solver_output: dict[str, Any] = field(default_factory=dict)
    perfil_carga_l: list[float] = field(default_factory=list)
    carga_viva_max_l: float = 0.0
    pico_parada_idx: int = 0
    total_retornable_l: float = 0.0


@dataclass
class FleetSolution:
    """Resultado de optimizar una flota de múltiples camiones (CVRP)."""
    routes: list[list[Stop]]                 # Una lista de paradas por camión
    route_metrics: list[dict]                # Métricas por ruta: time_s, distance_m
    total_time_s: int                        # Suma de tiempos de todas las rutas
    total_distance_m: int                    # Suma de distancias de todas las rutas
    status: str                              # "OPTIMAL" | "FEASIBLE" | "INFEASIBLE"
    n_vehicles_used: int                     # Camiones realmente asignados (puede ser < n_vehicles)
    raw_solver_output: dict[str, Any] = field(default_factory=dict)


_OR_STATUS = {
    0: "ROUTING_NOT_SOLVED",
    1: "OPTIMAL",
    2: "ROUTING_PARTIAL_SUCCESS_LOCAL_OPTIMUM_NOT_REACHED",
    3: "ROUTING_FAIL",
    4: "ROUTING_FAIL_TIMEOUT",
    5: "ROUTING_INVALID",
    6: "ROUTING_INFEASIBLE",
}


def _truck_capacity(truck: str) -> tuple[float, float]:
    spec = config.TRUCKS[truck]
    return spec["vol_m3"] * 1000.0, float(spec["peso_max_kg"])


def compute_carga_viva_profile(
    ordered_stops: list[Stop],
) -> tuple[list[float], float, int, float]:
    """Devuelve (perfil, pico_l, pico_idx, total_ret_l) para una ruta."""
    if not ordered_stops:
        return [0.0], 0.0, 0, 0.0
    total_vol = sum(s.volumen_l for s in ordered_stops)
    perfil: list[float] = [round(total_vol, 3)]
    carga = total_vol
    pico = total_vol
    pico_idx = 0
    for k, s in enumerate(ordered_stops, 1):
        carga = carga - float(s.volumen_l) + float(s.volumen_retornable_l)
        carga = max(0.0, carga)
        perfil.append(round(carga, 3))
        if carga > pico:
            pico = carga
            pico_idx = k
    total_ret = sum(s.volumen_retornable_l for s in ordered_stops)
    return perfil, float(pico), int(pico_idx), float(total_ret)


def _diagnose_infeasible_windows(
    stops: list[Stop], time_int: np.ndarray, depot_open_s: int
) -> list[dict]:
    issues = []
    for k, s in enumerate(stops, 1):
        if s.ventana_inicio is None or s.ventana_fin is None:
            continue
        earliest_arrival = depot_open_s + int(time_int[0, k])
        if earliest_arrival > s.ventana_fin:
            issues.append({
                "cliente_id": s.cliente_id,
                "cliente_nombre": s.cliente_nombre,
                "ventana_fin_s": s.ventana_fin,
                "earliest_arrival_s": earliest_arrival,
            })
    return issues


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
    depot_open_s: int = config.JORNADA_INICIO_S,
    depot_close_s: int = config.JORNADA_FIN_S,
) -> Solution:
    """Resuelve un VRP de 1 camion."""
    n_stops = len(stops)
    if n_stops == 0:
        return Solution([], 0, 0, "OPTIMAL", {"trivial": "empty"})

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

    n_nodes = n_stops + 1
    if time_matrix_s.shape != (n_nodes, n_nodes) or dist_matrix_m.shape != (n_nodes, n_nodes):
        raise DammSmartTruckError(
            f"Dimensiones inesperadas: time={time_matrix_s.shape}, "
            f"dist={dist_matrix_m.shape}, esperaba ({n_nodes},{n_nodes})"
        )

    time_int = np.rint(time_matrix_s).astype(np.int64)
    dist_int = np.rint(dist_matrix_m).astype(np.int64)
    service = [0] + [int(s.tiempo_servicio_s) for s in stops]
    demand_vol = [0] + [int(round(s.volumen_l)) for s in stops]
    demand_kg = [0] + [int(round(s.peso_kg)) for s in stops]
    cap_vol = int(round(truck_capacity_l))
    cap_kg = int(round(truck_capacity_kg))

    if use_time_windows:
        unreachable = _diagnose_infeasible_windows(stops, time_int, depot_open_s)
        if unreachable:
            logger.warning("Paradas inalcanzables por ventana: {}", len(unreachable))
            return Solution(
                ordered_stops=list(stops),
                total_time_s=0, total_distance_m=0, status="INFEASIBLE",
                raw_solver_output={"reason": "time_window_unreachable",
                                   "stops": unreachable},
            )

    manager = pywrapcp.RoutingIndexManager(n_nodes, 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    def transit_time_cb(from_idx: int, to_idx: int) -> int:
        i = manager.IndexToNode(from_idx)
        j = manager.IndexToNode(to_idx)
        return service[i] + int(time_int[i, j])

    transit_cb_idx = routing.RegisterTransitCallback(transit_time_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb_idx)

    if use_time_windows:
        max_slack = max(0, depot_close_s - depot_open_s)
        routing.AddDimension(
            transit_cb_idx,
            int(max_slack),
            int(depot_close_s),
            False,
            "Time",
        )
        time_dim = routing.GetDimensionOrDie("Time")
        depot_start_index = routing.Start(0)
        depot_end_index = routing.End(0)
        time_dim.CumulVar(depot_start_index).SetRange(int(depot_open_s), int(depot_close_s))
        time_dim.CumulVar(depot_end_index).SetRange(int(depot_open_s), int(depot_close_s))
        for k, s in enumerate(stops, 1):
            if s.ventana_inicio is None or s.ventana_fin is None:
                continue
            idx = manager.NodeToIndex(k)
            ini = max(int(s.ventana_inicio), int(depot_open_s))
            fin = min(int(s.ventana_fin), int(depot_close_s))
            if ini > fin:
                fin = ini
            time_dim.CumulVar(idx).SetRange(ini, fin)
    else:
        routing.AddDimension(
            transit_cb_idx, 0, int(max_route_time_s), True, "Time",
        )

    def demand_vol_cb(from_idx: int) -> int:
        return demand_vol[manager.IndexToNode(from_idx)]
    vol_cb_idx = routing.RegisterUnaryTransitCallback(demand_vol_cb)
    routing.AddDimensionWithVehicleCapacity(vol_cb_idx, 0, [cap_vol], True, "Volumen")

    def demand_kg_cb(from_idx: int) -> int:
        return demand_kg[manager.IndexToNode(from_idx)]
    kg_cb_idx = routing.RegisterUnaryTransitCallback(demand_kg_cb)
    routing.AddDimensionWithVehicleCapacity(kg_cb_idx, 0, [cap_kg], True, "Peso")

    if use_pickup_delivery:
        demand_ret = [0] + [int(round(s.volumen_retornable_l)) for s in stops]
        total_ret_int = sum(demand_ret)
        if total_ret_int > cap_vol:
            logger.warning(
                "Retornables totales {}L exceden capacidad del camion {}L",
                total_ret_int, cap_vol,
            )
            return Solution(
                ordered_stops=list(stops),
                total_time_s=0, total_distance_m=0, status="INFEASIBLE",
                raw_solver_output={"reason": "returns_total_overflow",
                                   "total_retornable_l": total_ret_int,
                                   "cap_vol_l": cap_vol},
                total_retornable_l=float(total_ret_int),
            )

        def demand_ret_cb(from_idx: int) -> int:
            return demand_ret[manager.IndexToNode(from_idx)]
        ret_cb_idx = routing.RegisterUnaryTransitCallback(demand_ret_cb)
        routing.AddDimensionWithVehicleCapacity(
            ret_cb_idx, 0, [cap_vol], True, "Retornables",
        )

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = int(time_limit_s)

    raw_solution = routing.SolveWithParameters(params)
    or_status = routing.status()
    status_label = _OR_STATUS.get(or_status, f"UNKNOWN({or_status})")

    if raw_solution is None:
        return Solution(
            ordered_stops=list(stops),
            total_time_s=0, total_distance_m=0,
            status="INFEASIBLE" if or_status == 6 else status_label,
            raw_solver_output={"or_status": or_status, "label": status_label,
                               "reason": "no_solution_within_time_limit_or_infeasible"},
        )

    ordered: list[Stop] = []
    arrivals_s: list[int] = []
    total_time = 0
    total_dist = 0
    index = routing.Start(0)
    time_dim = routing.GetDimensionOrDie("Time") if use_time_windows else None

    start_time = raw_solution.Min(time_dim.CumulVar(index)) if time_dim else 0
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node != 0:
            ordered.append(stops[node - 1])
            if time_dim is not None:
                arrivals_s.append(int(raw_solution.Min(time_dim.CumulVar(index))))
        next_index = raw_solution.Value(routing.NextVar(index))
        i = manager.IndexToNode(index)
        j = manager.IndexToNode(next_index)
        total_time += service[i] + int(time_int[i, j])
        total_dist += int(dist_int[i, j])
        index = next_index

    raw_out = {
        "or_status": or_status,
        "objective": raw_solution.ObjectiveValue(),
        "depot_start_s": int(start_time),
    }
    if time_dim is not None:
        raw_out["depot_end_s"] = int(raw_solution.Min(time_dim.CumulVar(index)))
        raw_out["arrivals_s"] = arrivals_s

    perfil: list[float] = []
    pico_l = 0.0
    pico_idx = 0
    total_ret = 0.0
    if use_pickup_delivery:
        perfil, pico_l, pico_idx, total_ret = compute_carga_viva_profile(ordered)
        raw_out["perfil_carga_l"] = perfil
        raw_out["carga_viva_max_l"] = pico_l
        raw_out["pico_parada_idx"] = pico_idx
        raw_out["total_retornable_l"] = total_ret

        if pico_l > truck_capacity_l + 1e-6:
            logger.warning(
                "Carga viva pico {:.1f}L > capacidad {:.1f}L (parada {})",
                pico_l, truck_capacity_l, pico_idx,
            )
            raw_out["reason"] = "returns_pico_overflow"
            return Solution(
                ordered_stops=ordered,
                total_time_s=int(total_time),
                total_distance_m=int(total_dist),
                status="INFEASIBLE",
                raw_solver_output=raw_out,
                perfil_carga_l=perfil,
                carga_viva_max_l=pico_l,
                pico_parada_idx=pico_idx,
                total_retornable_l=total_ret,
            )

    return Solution(
        ordered_stops=ordered,
        total_time_s=int(total_time),
        total_distance_m=int(total_dist),
        status="OPTIMAL" if status_label in ("OPTIMAL", "ROUTING_SUCCESS") else status_label,
        raw_solver_output=raw_out,
        perfil_carga_l=perfil,
        carga_viva_max_l=pico_l,
        pico_parada_idx=pico_idx,
        total_retornable_l=total_ret,
    )


def solve_fleet(
    stops: list[Stop],
    depot: tuple[float, float],
    n_vehicles: int,
    truck_capacity_l: float,
    truck_capacity_kg: float,
    time_matrix_s: np.ndarray,
    dist_matrix_m: np.ndarray,
    *,
    max_route_time_s: int = 8 * 3600,
    time_limit_s: int = 20,
    use_time_windows: bool = False,
    depot_open_s: int = config.JORNADA_INICIO_S,
    depot_close_s: int = config.JORNADA_FIN_S,
) -> FleetSolution:
    """Resuelve un CVRP (Capacitated Vehicle Routing Problem) multi-camión.

    Todos los clientes deben entrar en la solución; se reparten entre n_vehicles
    camiones de igual capacidad. Mantiene todas las restricciones de capacidad
    (volumen + peso) y ventanas horarias de `solve_single_truck`.

    Retorna `FleetSolution` con:
    - `routes`: list[list[Stop]] — una ruta (lista de paradas) por camión
    - `route_metrics`: métricas por ruta (time_s, distance_m)
    - `total_time_s`, `total_distance_m`: suma agregada
    - `n_vehicles_used`: cuántos camiones se usaron realmente
    - `status`: "OPTIMAL", "FEASIBLE", o "INFEASIBLE"
    """
    n_stops = len(stops)
    if n_stops == 0:
        return FleetSolution([], [], 0, 0, "OPTIMAL", 0, {"trivial": "empty"})

    # Sanity check: capacidad total vs suma de demandas
    total_vol = sum(s.volumen_l for s in stops)
    total_peso = sum(s.peso_kg for s in stops)
    fleet_cap_vol = n_vehicles * truck_capacity_l
    fleet_cap_kg = n_vehicles * truck_capacity_kg
    if total_vol > fleet_cap_vol or total_peso > fleet_cap_kg:
        logger.warning(
            "Capacidad de flota excedida: vol={:.1f}L vs {:.1f}L | peso={:.1f}kg vs {:.1f}kg",
            total_vol, fleet_cap_vol, total_peso, fleet_cap_kg
        )
        return FleetSolution(
            routes=[],
            route_metrics=[],
            total_time_s=0, total_distance_m=0,
            status="INFEASIBLE",
            n_vehicles_used=n_vehicles,
            raw_solver_output={
                "reason": "fleet_capacity_overflow",
                "total_vol_l": total_vol, "total_peso_kg": total_peso,
                "fleet_cap_vol_l": fleet_cap_vol, "fleet_cap_kg": fleet_cap_kg,
            },
        )

    n_nodes = n_stops + 1  # 0 = depot
    if time_matrix_s.shape != (n_nodes, n_nodes) or dist_matrix_m.shape != (n_nodes, n_nodes):
        raise DammSmartTruckError(
            f"Dimensiones inesperadas: time={time_matrix_s.shape}, "
            f"dist={dist_matrix_m.shape}, esperaba ({n_nodes},{n_nodes})"
        )

    # Pre-cast a int para OR-Tools
    time_int = np.rint(time_matrix_s).astype(np.int64)
    dist_int = np.rint(dist_matrix_m).astype(np.int64)
    service = [0] + [int(s.tiempo_servicio_s) for s in stops]
    demand_vol = [0] + [int(round(s.volumen_l)) for s in stops]
    demand_kg = [0] + [int(round(s.peso_kg)) for s in stops]
    cap_vol = int(round(truck_capacity_l))
    cap_kg = int(round(truck_capacity_kg))

    # Pre-diagnóstico para ventanas inalcanzables (igual que single_truck)
    if use_time_windows:
        unreachable = _diagnose_infeasible_windows(stops, time_int, depot_open_s)
        if unreachable:
            logger.warning("Paradas inalcanzables por ventana: {}", len(unreachable))
            return FleetSolution(
                routes=[],
                route_metrics=[],
                total_time_s=0, total_distance_m=0,
                status="INFEASIBLE",
                n_vehicles_used=n_vehicles,
                raw_solver_output={"reason": "time_window_unreachable", "stops": unreachable},
            )

    # ---- Crear manager con n_vehicles vehículos ----
    manager = pywrapcp.RoutingIndexManager(n_nodes, n_vehicles, 0)  # depot at 0
    routing = pywrapcp.RoutingModel(manager)

    # ---- Callback de tiempo (service-at-source como en single_truck) ----
    def transit_time_cb(from_idx: int, to_idx: int) -> int:
        i = manager.IndexToNode(from_idx)
        j = manager.IndexToNode(to_idx)
        return service[i] + int(time_int[i, j])

    transit_cb_idx = routing.RegisterTransitCallback(transit_time_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb_idx)

    # ---- Dimensión de tiempo ----
    if use_time_windows:
        max_slack = max(0, depot_close_s - depot_open_s)
        routing.AddDimension(
            transit_cb_idx,
            int(max_slack),
            int(depot_close_s),
            False,
            "Time",
        )
        time_dim = routing.GetDimensionOrDie("Time")

        # Ventana del depot para cada vehículo
        for v in range(n_vehicles):
            depot_start_index = routing.Start(v)
            depot_end_index = routing.End(v)
            time_dim.CumulVar(depot_start_index).SetRange(int(depot_open_s), int(depot_close_s))
            time_dim.CumulVar(depot_end_index).SetRange(int(depot_open_s), int(depot_close_s))

        # Ventana por parada (igual para todos los vehículos)
        for k, s in enumerate(stops, 1):
            if s.ventana_inicio is None or s.ventana_fin is None:
                continue
            idx = manager.NodeToIndex(k)
            ini = max(int(s.ventana_inicio), int(depot_open_s))
            fin = min(int(s.ventana_fin), int(depot_close_s))
            if ini > fin:
                logger.warning("Ventana del cliente {} colapsada", s.cliente_id)
                fin = ini
            time_dim.CumulVar(idx).SetRange(ini, fin)
    else:
        # Dimensión Time relativa para limitar tiempo máximo por ruta
        routing.AddDimension(
            transit_cb_idx, 0, int(max_route_time_s), True, "Time",
        )

    # ---- Capacidad de volumen ----
    def demand_vol_cb(from_idx: int) -> int:
        return demand_vol[manager.IndexToNode(from_idx)]
    vol_cb_idx = routing.RegisterUnaryTransitCallback(demand_vol_cb)
    # Capacidades por vehículo (todos iguales para CVRP estándar)
    routing.AddDimensionWithVehicleCapacity(
        vol_cb_idx, 0, [cap_vol] * n_vehicles, True, "Volumen"
    )

    # ---- Capacidad de peso ----
    def demand_kg_cb(from_idx: int) -> int:
        return demand_kg[manager.IndexToNode(from_idx)]
    kg_cb_idx = routing.RegisterUnaryTransitCallback(demand_kg_cb)
    routing.AddDimensionWithVehicleCapacity(
        kg_cb_idx, 0, [cap_kg] * n_vehicles, True, "Peso"
    )

    # ---- Parámetros de búsqueda ----
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
        return FleetSolution(
            routes=[],
            route_metrics=[],
            total_time_s=0, total_distance_m=0,
            status="INFEASIBLE" if or_status == 6 else status_label,
            n_vehicles_used=0,
            raw_solver_output={
                "or_status": or_status, "label": status_label,
                "reason": "no_solution_within_time_limit_or_infeasible"
            },
        )

    # ---- Extraer rutas por vehículo ----
    routes: list[list[Stop]] = []
    route_metrics: list[dict] = []
    total_time_all = 0
    total_dist_all = 0
    time_dim = routing.GetDimensionOrDie("Time") if use_time_windows else None

    for v in range(n_vehicles):
        route_stops: list[Stop] = []
        route_arrivals: list[int] = []
        route_time = 0
        route_dist = 0

        index = routing.Start(v)
        start_time = raw_solution.Min(time_dim.CumulVar(index)) if time_dim else 0

        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            if node != 0:
                route_stops.append(stops[node - 1])
                if time_dim is not None:
                    route_arrivals.append(int(raw_solution.Min(time_dim.CumulVar(index))))

            next_index = raw_solution.Value(routing.NextVar(index))
            i = manager.IndexToNode(index)
            j = manager.IndexToNode(next_index)
            route_time += service[i] + int(time_int[i, j])
            route_dist += int(dist_int[i, j])
            index = next_index

        # Solo agregar ruta si tiene paradas
        if route_stops:
            routes.append(route_stops)
            route_metrics.append({
                "vehicle": v,
                "time_s": int(route_time),
                "distance_m": int(route_dist),
                "n_stops": len(route_stops),
                "arrivals_s": route_arrivals,
                "start_time_s": int(start_time),
            })
            total_time_all += route_time
            total_dist_all += route_dist

    n_vehicles_used = len(routes)
    return FleetSolution(
        routes=routes,
        route_metrics=route_metrics,
        total_time_s=int(total_time_all),
        total_distance_m=int(total_dist_all),
        status="OPTIMAL" if status_label in ("OPTIMAL", "ROUTING_SUCCESS") else status_label,
        n_vehicles_used=n_vehicles_used,
        raw_solver_output={
            "or_status": or_status,
            "objective": raw_solution.ObjectiveValue(),
            "n_vehicles_requested": n_vehicles,
            "n_vehicles_used": n_vehicles_used,
        },
    )


# ---------------------------------------------------------------------------
# Métricas baseline (orden real del dataset)
# ---------------------------------------------------------------------------
def baseline_metrics(stops: list[Stop], time_matrix_s: np.ndarray,
                     dist_matrix_m: np.ndarray) -> tuple[int, int]:
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
    total_t += int(time_int[prev, 0])
    total_d += int(dist_int[prev, 0])
    return total_t, total_d


def build_stops_from_transporte(
    transporte_id: int,
    canonical: pd.DataFrame,
    geocoding: pd.DataFrame,
) -> list[Stop]:
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
            materiales_json=str(getattr(r, "materiales_json", "[]")),
            n_materiales=int(getattr(r, "n_materiales", 0)),
            n_lineas=int(getattr(r, "n_lineas", 0)),
            pct_retornable=float(getattr(r, "pct_retornable", 0.0)),
        ))
    if skipped:
        logger.warning("Transporte {}: {} clientes sin geocoding (saltados)",
                       transporte_id, skipped)
    return stops


def attach_time_windows(
    stops: list[Stop],
    fecha,
    canonical: pd.DataFrame | None = None,
    horarios: pd.DataFrame | None = None,
) -> list[Stop]:
    if hasattr(fecha, "date"):
        fecha = fecha.date()
    windows = horarios_mod.windows_for_date(fecha, canonical=canonical, horarios=horarios)
    n_match = 0
    for s in stops:
        w = windows.get(int(s.cliente_id))
        if w is not None:
            s.ventana_inicio, s.ventana_fin = w
            n_match += 1
    if stops:
        logger.info("Ventanas horarias asignadas: {}/{} ({}%)",
                    n_match, len(stops), round(100 * n_match / len(stops)))
    return stops


def _format_hms(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:01d}h{m:02d}m{s:02d}s"


def _parse_materiales(blob: str) -> list[dict[str, Any]]:
    if not blob:
        return []
    try:
        data = json.loads(blob)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]


def _top_umas_for_stop(stop: Stop, top_k: int = 2) -> list[tuple[str, float]]:
    by_uma: dict[str, float] = {}
    for item in _parse_materiales(stop.materiales_json):
        uma = str(item.get("uma") or "UNK")
        by_uma[uma] = by_uma.get(uma, 0.0) + float(item.get("vol_l") or 0.0)
    return sorted(by_uma.items(), key=lambda x: x[1], reverse=True)[:top_k]


def _top_productos(stops: list[Stop], top_k: int = 5) -> list[dict[str, Any]]:
    by_material: dict[str, dict[str, Any]] = {}
    for s in stops:
        for item in _parse_materiales(s.materiales_json):
            material = str(item.get("material") or "UNKNOWN")
            if material not in by_material:
                by_material[material] = {
                    "material": material,
                    "denominacion": str(item.get("denominacion") or ""),
                    "vol_l": 0.0,
                    "peso_kg": 0.0,
                    "lineas": 0,
                    "retornable": bool(item.get("retornable", False)),
                }
            by_material[material]["vol_l"] += float(item.get("vol_l") or 0.0)
            by_material[material]["peso_kg"] += float(item.get("peso_kg") or 0.0)
            by_material[material]["lineas"] += 1
    top = sorted(by_material.values(), key=lambda x: x["vol_l"], reverse=True)[:top_k]
    for row in top:
        row["vol_l"] = round(float(row["vol_l"]), 3)
        row["peso_kg"] = round(float(row["peso_kg"]), 3)
    return top


def _route_snapshot(transporte_id: int, truck: str, mode: str, payload: dict[str, Any]) -> str:
    """Devuelve la ruta del snapshot (sin guardarlo por ahora).
    
    Para futura UI: descomentar la linea de write_text cuando sea necesario.
    De momento, el payload se muestra bonito en terminal.
    """
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = config.CACHE_DIR / f"route_snapshot_{transporte_id}_{truck}_{mode}.json"
    # COMENTADO PARA AHORA: path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _print_route_summary_single(payload: dict[str, Any], loading_html_path: str | None = None) -> None:
    """Imprime en terminal un resumen COMPLETO con insights, visualización y recomendaciones."""
    print("\n" + "="*90)
    print("SOLUCION OPTIMIZADA: RUTA SINGLE TRUCK")
    print("="*90)
    
    # ===== BLOQUE 1: RESUMEN EJECUTIVO =====
    print(f"\n[TRANSPORTE {payload['transporte']}] Camion {payload['truck']} | Status: {payload['status']}")
    
    metrics = payload["metrics"]
    print(f"\n[RESULTADOS OPTIMIZACION]")
    print(f"  Baseline (orden real):    {metrics['baseline_time_s']//3600}h{(metrics['baseline_time_s']%3600)//60:02d}m | {metrics['baseline_dist_m']/1000:.2f}km")
    print(f"  Optimizado (OR-Tools):    {metrics['opt_time_s']//3600}h{(metrics['opt_time_s']%3600)//60:02d}m | {metrics['opt_dist_m']/1000:.2f}km")
    print(f"  MEJORA:                   -{abs(metrics['delta_time_pct']):.1f}% tiempo | -{abs(metrics['delta_dist_pct']):.1f}% distancia")
    
    print(f"\n[CARGA DEL CAMION]")
    print(f"  Volumen entrega:  {metrics['entrega_total_l']:7.1f}L")
    print(f"  Volumen recogida: {metrics['recogida_total_l']:7.1f}L")
    print(f"  TOTAL:            {metrics['entrega_total_l'] + metrics['recogida_total_l']:7.1f}L / {payload.get('truck_capacity_l', 14400)}L")
    
    # ===== BLOQUE 2: PARADAS Y MATERIALES =====
    print(f"\n[PARADAS DETALLE] ({len(payload.get('stops', []))} paradas)")
    print("-"*90)
    stops = payload.get("stops", [])
    for stop in stops:
        mat_summary = ""
        if stop["materiales"]:
            uma_summary = {}
            for mat in stop["materiales"]:
                uma = mat.get('uma', 'UN')
                if uma not in uma_summary:
                    uma_summary[uma] = 0
                uma_summary[uma] += mat.get('vol_l', 0)
            mat_summary = " | ".join([f"{uma}:{vol:.0f}L" for uma, vol in uma_summary.items()])
        
        print(f"  {stop['order']:2d}. {stop['cliente_nombre'][:30]:30s} {stop['poblacion'][:12]:12s} "
              f"ENT:{stop['entrega_l']:6.1f}L REC:{stop['recogida_l']:6.1f}L | {mat_summary}")
    
    # ===== BLOQUE 3: VISUALIZACIÓN DE CARGA =====
    print("\n" + "="*90)
    loading_plan = visualize_loading_plan(stops, int(payload.get('truck_capacity_l', 14400)))
    print(format_loading_plan_for_terminal(loading_plan))
    if loading_html_path:
        out_html = export_loading_plan_html(
            loading_plan,
            output_path=loading_html_path,
            title=f"Plan de carga - Transporte {payload['transporte']} ({payload['truck']})",
        )
        print(f"\n[HTML] Visualizacion de carga exportada en: {out_html}")
    
    # ===== BLOQUE 4: INSIGHTS Y RECOMENDACIONES =====
    print("\n" + "="*90)
    insight = analyze_route(
        transporte_id=payload['transporte'],
        stops=stops,
        baseline_time_s=metrics['baseline_time_s'],
        baseline_dist_m=metrics['baseline_dist_m'],
        opt_time_s=metrics['opt_time_s'],
        opt_dist_m=metrics['opt_dist_m'],
        truck_capacity_l=payload.get('truck_capacity_l', 14400),
        truck_capacity_kg=payload.get('truck_capacity_kg', 6500),
        total_entrega_l=metrics['entrega_total_l'],
        total_recogida_l=metrics['recogida_total_l'],
    )
    print(format_insights_for_terminal(insight))
    
    print("="*90 + "\n")


def _print_route_summary_fleet(payload: dict[str, Any], loading_html_path: str | None = None) -> None:
    """Imprime en terminal un resumen de FLOTA con insights agregados."""
    print("\n" + "="*90)
    print("SOLUCION OPTIMIZADA: FLOTA MULTI-VEHICULO")
    print("="*90)
    
    print(f"\n[TRANSPORTE {payload['transporte']}] Flota de {payload['vehicles_requested']} camiones {payload['truck']} "
          f"| Usados: {payload['vehicles_used']} | Status: {payload['status']}")
    
    metrics = payload["metrics"]
    print(f"\n[RESULTADOS OPTIMIZACION]")
    print(f"  Baseline (orden real):    {metrics['baseline_time_s']//3600}h{(metrics['baseline_time_s']%3600)//60:02d}m | {metrics['baseline_dist_m']/1000:.2f}km")
    print(f"  Optimizado (CVRP):        {metrics['opt_time_s']//3600}h{(metrics['opt_time_s']%3600)//60:02d}m | {metrics['opt_dist_m']/1000:.2f}km")
    print(f"  MEJORA:                   -{abs(metrics['delta_time_pct']):.1f}% tiempo | -{abs(metrics['delta_dist_pct']):.1f}% distancia")
    
    routes = payload.get("routes", [])
    print(f"\n[DISTRIBUCION POR VEHICULO]")
    print("-"*90)
    
    all_stops = []
    for route in routes:
        stops = route.get("stops", [])
        all_stops.extend(stops)
        print(f"  Vehiculo {route['vehicle_index']}: {route['n_stops']:2d} paradas | "
              f"{route['time_s']//3600}h{(route['time_s']%3600)//60:02d}m | "
              f"{route['distance_m']/1000:.1f}km | "
              f"Carga: {route['entrega_total_l']:.0f}L entrega / {route['recogida_total_l']:.0f}L recogida")
    
    # Insights agregados
    if all_stops:
        loading_plan = visualize_loading_plan(all_stops, int(payload.get('truck_capacity_l', 14400)))
        if loading_html_path:
            out_html = export_loading_plan_html(
                loading_plan,
                output_path=loading_html_path,
                title=f"Plan de carga flota - Transporte {payload['transporte']} ({payload['truck']})",
            )
            print(f"\n[HTML] Visualizacion de carga flota exportada en: {out_html}")

        print("\n" + "="*90)
        insight = analyze_route(
            transporte_id=payload['transporte'],
            stops=all_stops,
            baseline_time_s=metrics['baseline_time_s'],
            baseline_dist_m=metrics['baseline_dist_m'],
            opt_time_s=metrics['opt_time_s'],
            opt_dist_m=metrics['opt_dist_m'],
            truck_capacity_l=payload.get('truck_capacity_l', 14400),
            truck_capacity_kg=payload.get('truck_capacity_kg', 6500),
            total_entrega_l=metrics['entrega_total_l'],
            total_recogida_l=metrics['recogida_total_l'],
        )
        print(format_insights_for_terminal(insight))
    
    print("="*90 + "\n")


def _print_explainability_single(sol: Solution, stops: list[Stop], explain_lang: str) -> dict[str, str]:
    try:
        from src.explain import explain_solution
        explanations = explain_solution(sol, stops, aspect="all", language=explain_lang)
    except Exception as exc:
        logger.warning("No se pudo generar explicabilidad (single): {}", exc)
        return {}

    print("\n" + "=" * 90)
    print("EXPLICABILIDAD DE LA SOLUCION")
    print("=" * 90)
    if explanations.get("route"):
        print("\n[ROUTE EXPLANATION]")
        print(explanations["route"])
    if explanations.get("packaging"):
        print("\n[PACKAGING EXPLANATION]")
        print(explanations["packaging"])
    print("=" * 90 + "\n")
    return explanations


def _print_explainability_fleet(sol: FleetSolution, stops: list[Stop], explain_lang: str) -> dict[str, Any]:
    try:
        from src.explain import explain_fleet_solution
        explanations = explain_fleet_solution(sol, stops, language=explain_lang)
    except Exception as exc:
        logger.warning("No se pudo generar explicabilidad (fleet): {}", exc)
        return {}

    print("\n" + "=" * 90)
    print("EXPLICABILIDAD DE FLOTA")
    print("=" * 90)
    if explanations.get("fleet"):
        print("\n[FLEET EXPLANATION]")
        print(explanations["fleet"])
    routes_text = explanations.get("routes", [])
    if routes_text:
        print("\n[ROUTES SUMMARY]")
        for row in routes_text:
            print(f"  - {row}")
    print("=" * 90 + "\n")
    return explanations


def run_for_transporte(
    transporte_id: int,
    truck: str = "6P",
    time_limit_s: int = 20,
    use_time_windows: bool = False,
    use_pickup_delivery: bool = False,
    explain: bool = False,
    explain_lang: str = "es",
    loading_html: str | None = None,
) -> dict:
    canonical = pd.read_parquet(config.CANONICAL_PARQUET)
    geo = pd.read_parquet(config.GEOCODING_PARQUET)

    stops = build_stops_from_transporte(transporte_id, canonical, geo)
    if not stops:
        raise DammSmartTruckError(f"Transporte {transporte_id} sin paradas geocodificadas")

    fecha = canonical[canonical["transporte"] == transporte_id]["fecha"].iloc[0]
    if use_time_windows:
        attach_time_windows(stops, fecha, canonical=canonical)

    coords = [(config.DEPOT_LAT, config.DEPOT_LNG)] + [(s.lat, s.lng) for s in stops]
    time_mat, dist_mat = distance_matrix.get_matrix(coords)

    cap_l, cap_kg = _truck_capacity(truck)
    base_time, base_dist = baseline_metrics(stops, time_mat, dist_mat)
    sol = solve_single_truck(
        stops, (config.DEPOT_LAT, config.DEPOT_LNG),
        cap_l, cap_kg, time_mat, dist_mat,
        time_limit_s=time_limit_s,
        use_time_windows=use_time_windows,
        use_pickup_delivery=use_pickup_delivery,
    )

    delta_t = sol.total_time_s - base_time
    delta_d = sol.total_distance_m - base_dist
    pct_t = 100 * delta_t / base_time if base_time else 0.0
    pct_d = 100 * delta_d / base_dist if base_dist else 0.0

    print(f"\n=== Transporte {transporte_id} | camion {truck} | {len(stops)} paradas ===")
    print(f"Status solver: {sol.status}")
    print(f"Baseline: tiempo {_format_hms(base_time)}  distancia {base_dist/1000:.2f} km")
    print(f"Optimo:   tiempo {_format_hms(sol.total_time_s)}  distancia {sol.total_distance_m/1000:.2f} km")
    print(f"Delta: tiempo {pct_t:+.2f}%   distancia {pct_d:+.2f}%")
    total_entrega = sum(s.volumen_l for s in sol.ordered_stops)
    total_recogida = sum(s.volumen_retornable_l for s in sol.ordered_stops)
    print("\nDetalle de carga:")
    print(f"  entrega total:  {total_entrega:.1f} L")
    print(f"  recogida total: {total_recogida:.1f} L")
    print("  Top productos por volumen:")
    for p in _top_productos(sol.ordered_stops, top_k=5):
        den = f" - {p['denominacion'][:28]}" if p["denominacion"] else ""
        ret = " RET" if p["retornable"] else ""
        print(f"    {p['material']}{den}: {p['vol_l']:.1f} L | {p['peso_kg']:.1f} kg | {p['lineas']} líneas{ret}")
    if use_pickup_delivery:
        cap_l_int = int(round(cap_l))
        total_entregable = total_entrega or 1.0
        print(f"\nLogistica inversa:")
        print(f"  total entregable: {total_entregable:.1f} L")
        print(f"  total retornable: {sol.total_retornable_l:.1f} L "
              f"({100*sol.total_retornable_l/total_entregable:.1f}%)")
        print(f"  carga viva pico:  {sol.carga_viva_max_l:.1f} L "
              f"(parada {sol.pico_parada_idx}/{len(sol.ordered_stops)}, cap {cap_l_int})")

    snapshot_payload = {
        "transporte": transporte_id,
        "truck": truck,
        "mode": "single",
        "status": sol.status,
        "truck_capacity_l": cap_l,
        "truck_capacity_kg": cap_kg,
        "metrics": {
            "baseline_time_s": base_time,
            "baseline_dist_m": base_dist,
            "opt_time_s": sol.total_time_s,
            "opt_dist_m": sol.total_distance_m,
            "delta_time_pct": round(pct_t, 3),
            "delta_dist_pct": round(pct_d, 3),
            "entrega_total_l": round(total_entrega, 3),
            "recogida_total_l": round(total_recogida, 3),
        },
        "top_productos": _top_productos(sol.ordered_stops, top_k=10),
        "stops": [
            {
                "order": idx,
                "cliente_id": int(s.cliente_id),
                "cliente_nombre": s.cliente_nombre,
                "poblacion": s.poblacion,
                "lat": float(s.lat),
                "lng": float(s.lng),
                "entrega_id": int(s.entrega_id) if s.entrega_id is not None else None,
                "entrega_l": round(float(s.volumen_l), 3),
                "recogida_l": round(float(s.volumen_retornable_l), 3),
                "peso_kg": round(float(s.peso_kg), 3),
                "n_materiales": int(s.n_materiales),
                "n_lineas": int(s.n_lineas),
                "pct_retornable": round(float(s.pct_retornable), 4),
                "materiales": _parse_materiales(s.materiales_json),
            }
            for idx, s in enumerate(sol.ordered_stops, 1)
        ],
    }
    snapshot_path = _route_snapshot(transporte_id, truck, "single", snapshot_payload)
    # print(f"\nSnapshot ruta guardado en: {snapshot_path}")
    if loading_html == "auto":
        default_html = str(config.CACHE_DIR / f"loading_plan_{transporte_id}_{truck}_single.html")
    elif isinstance(loading_html, str) and loading_html:
        default_html = loading_html
    else:
        default_html = None
    _print_route_summary_single(snapshot_payload, loading_html_path=default_html)

    explanations: dict[str, str] = {}
    if explain:
        explanations = _print_explainability_single(sol, stops, explain_lang)

    # Calcular peso total
    total_peso_kg = sum(s.peso_kg for s in sol.ordered_stops)

    return {
        "transporte": transporte_id,
        "n_stops": len(stops),
        "baseline_time_s": base_time, "baseline_dist_m": base_dist,
        "opt_time_s": sol.total_time_s, "opt_dist_m": sol.total_distance_m,
        "delta_time_pct": pct_t, "delta_dist_pct": pct_d,
        "status": sol.status,
        "carga_viva_max_l": sol.carga_viva_max_l,
        "total_retornable_l": sol.total_retornable_l,
        "entrega_total_l": total_entrega,
        "recogida_total_l": total_recogida,
        "peso_kg": total_peso_kg,
        "snapshot_path": snapshot_path,
        "snapshot_payload": snapshot_payload,
        "loading_html_path": default_html,
        "explanations": explanations,
    }


def run_for_fleet(
    transporte_id: int,
    n_vehicles: int,
    truck: str = "6P",
    time_limit_s: int = 20,
    use_time_windows: bool = False,
    explain: bool = False,
    explain_lang: str = "es",
    loading_html: str | None = None,
) -> dict:
    """Ejecuta solve_fleet para un transporte, usando n_vehicles camiones.

    Similar a `run_for_transporte` pero divide los clientes automáticamente entre
    múltiples vehículos en lugar de forzar todo en uno.
    """
    canonical = pd.read_parquet(config.CANONICAL_PARQUET)
    geo = pd.read_parquet(config.GEOCODING_PARQUET)

    stops = build_stops_from_transporte(transporte_id, canonical, geo)
    if not stops:
        raise DammSmartTruckError(f"Transporte {transporte_id} sin paradas geocodificadas")

    fecha = canonical[canonical["transporte"] == transporte_id]["fecha"].iloc[0]
    if use_time_windows:
        attach_time_windows(stops, fecha, canonical=canonical)

    coords = [(config.DEPOT_LAT, config.DEPOT_LNG)] + [(s.lat, s.lng) for s in stops]
    time_mat, dist_mat = distance_matrix.get_matrix(coords)

    cap_l, cap_kg = _truck_capacity(truck)
    base_time, base_dist = baseline_metrics(stops, time_mat, dist_mat)
    sol = solve_fleet(
        stops, (config.DEPOT_LAT, config.DEPOT_LNG),
        n_vehicles,
        cap_l, cap_kg, time_mat, dist_mat,
        time_limit_s=time_limit_s,
        use_time_windows=use_time_windows,
    )

    delta_t = sol.total_time_s - base_time
    delta_d = sol.total_distance_m - base_dist
    pct_t = 100 * delta_t / base_time if base_time else 0.0
    pct_d = 100 * delta_d / base_dist if base_dist else 0.0

    print(f"\n{'='*80}")
    print(f"Transporte {transporte_id} | Flota de {n_vehicles} camiones ({truck})")
    print(f"{len(stops)} paradas totales | Status: {sol.status}")
    print(f"{'='*80}")

    print(f"\nBaseline (orden real, sin optimizar):")
    print(f"  tiempo:    {_format_hms(base_time)}  ({base_time} s)")
    print(f"  distancia: {base_dist/1000:8.2f} km")

    print(f"\nOptimizado (CVRP, {sol.n_vehicles_used} vehículos usados):")
    print(f"  tiempo:    {_format_hms(sol.total_time_s)}  ({sol.total_time_s} s)")
    print(f"  distancia: {sol.total_distance_m/1000:8.2f} km")

    print(f"\nDelta vs baseline:")
    print(f"  tiempo:    {delta_t:+d} s   ({pct_t:+.2f}%)")
    print(f"  distancia: {delta_d:+d} m   ({pct_d:+.2f}%)")

    print(f"\n{'-'*80}")
    print("Rutas por vehículo:")
    print(f"{'-'*80}")

    for vehicle_idx, (route_stops, metrics) in enumerate(zip(sol.routes, sol.route_metrics)):
        arrivals = metrics.get("arrivals_s", [])
        print(f"\nVehículo {vehicle_idx + 1}:")
        print(f"  Paradas: {metrics['n_stops']}")
        print(f"  Tiempo:  {_format_hms(metrics['time_s'])}  ({metrics['time_s']} s)")
        print(f"  Distancia: {metrics['distance_m']/1000:8.2f} km")

        vol_total = sum(s.volumen_l for s in route_stops)
        kg_total = sum(s.peso_kg for s in route_stops)
        cap_status_vol = f"{vol_total:.0f}/{cap_l:.0f}L"
        cap_status_kg = f"{kg_total:.0f}/{cap_kg:.0f}kg"
        print(f"  Carga:   {cap_status_vol:>15} vol  |  {cap_status_kg:>15} peso")
        print(f"  Entrega/recogida: {vol_total:.1f} L / {sum(s.volumen_retornable_l for s in route_stops):.1f} L")

        top_route_products = _top_productos(route_stops, top_k=3)
        if top_route_products:
            print("  Top productos (volumen):")
            for p in top_route_products:
                den = f" - {p['denominacion'][:24]}" if p["denominacion"] else ""
                print(f"    {p['material']}{den}: {p['vol_l']:.1f} L")

        print(f"  Clientes (id | nombre | población | entrega | recogida | top UMAs):")
        for k, s in enumerate(route_stops, 1):
            arr = f" llega {_format_hms(arrivals[k-1])}" if k - 1 < len(arrivals) else ""
            top_umas = ", ".join(f"{u}:{v:.0f}L" for u, v in _top_umas_for_stop(s)) or "-"
            print(f"    {k:2d}. {s.cliente_id} | {s.cliente_nombre[:24]:24s} | "
                  f"{s.poblacion[:12]:12s} | {s.volumen_l:7.1f} L | "
                  f"{s.volumen_retornable_l:7.1f} L | {top_umas}{arr}")

    fleet_snapshot_payload = {
        "transporte": transporte_id,
        "truck": truck,
        "mode": "fleet",
        "status": sol.status,
        "vehicles_requested": int(n_vehicles),
        "vehicles_used": int(sol.n_vehicles_used),
        "truck_capacity_l": cap_l,
        "truck_capacity_kg": cap_kg,
        "metrics": {
            "baseline_time_s": base_time,
            "baseline_dist_m": base_dist,
            "opt_time_s": sol.total_time_s,
            "opt_dist_m": sol.total_distance_m,
            "delta_time_pct": round(pct_t, 3),
            "delta_dist_pct": round(pct_d, 3),
            "entrega_total_l": round(sum(s.volumen_l for s in stops), 3),
            "recogida_total_l": round(sum(s.volumen_retornable_l for s in stops), 3),
        },
        "routes": [],
    }
    for vehicle_idx, (route_stops, metrics) in enumerate(zip(sol.routes, sol.route_metrics), 1):
        fleet_snapshot_payload["routes"].append({
            "vehicle_index": vehicle_idx,
            "n_stops": int(metrics.get("n_stops", len(route_stops))),
            "time_s": int(metrics.get("time_s", 0)),
            "distance_m": int(metrics.get("distance_m", 0)),
            "entrega_total_l": round(sum(s.volumen_l for s in route_stops), 3),
            "recogida_total_l": round(sum(s.volumen_retornable_l for s in route_stops), 3),
            "top_productos": _top_productos(route_stops, top_k=10),
            "stops": [
                {
                    "order": idx,
                    "cliente_id": int(s.cliente_id),
                    "cliente_nombre": s.cliente_nombre,
                    "poblacion": s.poblacion,
                    "lat": float(s.lat),
                    "lng": float(s.lng),
                    "entrega_l": round(float(s.volumen_l), 3),
                    "recogida_l": round(float(s.volumen_retornable_l), 3),
                    "peso_kg": round(float(s.peso_kg), 3),
                    "entrega_id": int(s.entrega_id) if s.entrega_id is not None else None,
                    "materiales": _parse_materiales(s.materiales_json),
                }
                for idx, s in enumerate(route_stops, 1)
            ],
        })
    fleet_snapshot_path = _route_snapshot(transporte_id, truck, "fleet", fleet_snapshot_payload)
    # print(f"\nSnapshot flota guardado en: {fleet_snapshot_path}")
    if loading_html == "auto":
        default_html = str(config.CACHE_DIR / f"loading_plan_{transporte_id}_{truck}_fleet.html")
    elif isinstance(loading_html, str) and loading_html:
        default_html = loading_html
    else:
        default_html = None
    _print_route_summary_fleet(fleet_snapshot_payload, loading_html_path=default_html)

    explanations: dict[str, Any] = {}
    if explain:
        explanations = _print_explainability_fleet(sol, stops, explain_lang)

    # Calcular totales de toda la flota
    total_entrega_fleet = sum(s.volumen_l for s in stops)
    total_recogida_fleet = sum(s.volumen_retornable_l for s in stops)
    total_peso_fleet = sum(s.peso_kg for s in stops)

    return {
        "transporte": transporte_id,
        "n_stops": len(stops),
        "n_vehicles_requested": n_vehicles,
        "n_vehicles_used": sol.n_vehicles_used,
        "baseline_time_s": base_time, "baseline_dist_m": base_dist,
        "opt_time_s": sol.total_time_s, "opt_dist_m": sol.total_distance_m,
        "delta_time_pct": pct_t, "delta_dist_pct": pct_d,
        "status": sol.status,
        "routes_count": len(sol.routes),
        "entrega_total_l": total_entrega_fleet,
        "recogida_total_l": total_recogida_fleet,
        "peso_kg": total_peso_fleet,
        "snapshot_path": fleet_snapshot_path,
        "snapshot_payload": fleet_snapshot_payload,
        "loading_html_path": default_html,
        "explanations": explanations,
    }


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", type=int, required=True,
                        help="ID de transporte (e.g. 11561535)")
    parser.add_argument("--fleet", type=int, default=None,
                        help="Número de vehículos (activa solve_fleet en lugar de solve_single_truck)")
    parser.add_argument("--truck", default="6P", choices=list(config.TRUCKS.keys()))
    parser.add_argument("--time-limit", type=int, default=20)
    parser.add_argument("--time-windows", action="store_true")
    parser.add_argument("--reverse-logistics", action="store_true")
    parser.add_argument("--explain", action="store_true",
                        help="Genera explicabilidad automatica (LLM o fallback)")
    parser.add_argument("--explain-lang", default="es", choices=["es", "en"],
                        help="Idioma de explicabilidad")
    parser.add_argument("--loading-html", default=None,
                        help="Ruta HTML de visualizacion de carga. Usa 'auto' para cache/...")
    args = parser.parse_args()

    if args.fleet is not None:
        run_for_fleet(args.transport, n_vehicles=args.fleet, truck=args.truck,
                      time_limit_s=args.time_limit, use_time_windows=args.time_windows,
                      explain=args.explain, explain_lang=args.explain_lang,
                      loading_html=args.loading_html)
    else:
        run_for_transporte(args.transport, truck=args.truck,
                           time_limit_s=args.time_limit,
                           use_time_windows=args.time_windows,
                           use_pickup_delivery=args.reverse_logistics,
                           explain=args.explain, explain_lang=args.explain_lang,
                           loading_html=args.loading_html)


if __name__ == "__main__":
    _main()
