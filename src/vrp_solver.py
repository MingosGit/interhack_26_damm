"""Solver VRP con OR-Tools para los camiones de DDI.

- MVP 4: 1 camión, capacidad de volumen + peso, minimizar tiempo total.
- MVP 5: ventanas horarias (`use_time_windows=True`) con la convención de
  *service-at-source* (CumulVar(j) = tiempo de **llegada** a j), que es lo que
  espera la documentación de OR-Tools para VRPTW.

**Heurística**:
- ``PATH_CHEAPEST_ARC`` como solución inicial: greedy desde el depot, elige el
  arco más barato a cada paso. Rápido y razonable para ruta única.
- ``GUIDED_LOCAL_SEARCH`` como metaheurística: penaliza arcos caros para
  escapar de óptimos locales. Es la combinación recomendada por la
  documentación oficial de OR-Tools para VRP de tamaño medio (≤ 100 nodos).
- ``time_limit`` por defecto 20 s — suficiente para ≤ 25 paradas en local.

Uso:
    python -m src.vrp_solver --transport 11561535 [--time-limit 20] [--truck 6P]
                              [--time-windows]
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from src import config, distance_matrix, horarios as horarios_mod
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
    cp: str = ""
    zona_dd: str = ""
    entrega_id: int | None = None
    materiales: list[dict] = field(default_factory=list)


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

def _diagnose_infeasible_windows(
    stops: list[Stop], time_int: np.ndarray, depot_open_s: int
) -> list[dict]:
    """Detecta paradas obviamente inalcanzables (necesario, no suficiente).

    Una parada es inalcanzable si el tiempo directo depot→parada+servicio
    desde la apertura del depósito ya supera su `ventana_fin`.
    """
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
    """Resuelve un VRP de 1 camión con capacidad de volumen y peso.

    `time_matrix_s` y `dist_matrix_m` son matrices ``(N+1)x(N+1)`` indexadas como
    ``0=depot`` y ``1..N`` = paradas en el orden de `stops`. Sus valores deben
    venir ya en segundos y metros enteros (se castean a int).

    Si ``use_time_windows=True``, se aplica la convención *service-at-source*:
    la dimensión Time mide tiempo absoluto (segundos desde medianoche) y
    cada CumulVar(j) representa la **hora de llegada** a la parada j. Las
    ventanas se aplican vía `CumulVar.SetRange`.

    Si ``use_pickup_delivery=True`` (MVP 7), se modela la ocupación como una
    dimensión adicional. En cada parada k se descuenta `volumen_l` (entregado)
    y se suma `RATIO_RETORNO_DEFECTO * volumen_retornable_l` (recogido). La
    ocupación nunca puede exceder la capacidad volumétrica del camión.
    """

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

    # Si VRPTW activo: pre-diagnóstico de ventanas obviamente inalcanzables.
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

    # ---- coste = servicio en origen + tiempo de transit (service-at-source) ----
    # Con esta convención CumulVar(j) = tiempo de llegada a j.
    def transit_time_cb(from_idx: int, to_idx: int) -> int:
        i = manager.IndexToNode(from_idx)
        j = manager.IndexToNode(to_idx)
        return service[i] + int(time_int[i, j])

    transit_cb_idx = routing.RegisterTransitCallback(transit_time_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb_idx)

    if use_time_windows:
        # Dimensión absoluta: arranque en [depot_open_s, depot_close_s].
        # Slack permite esperar antes de un cliente con ventana posterior.
        max_slack = max(0, depot_close_s - depot_open_s)
        routing.AddDimension(
            transit_cb_idx,
            int(max_slack),
            int(depot_close_s),
            False,                  # NO forzar inicio = 0 (queremos absoluto)
            "Time",
        )
        time_dim = routing.GetDimensionOrDie("Time")

        # Ventana del depot.
        depot_start_index = routing.Start(0)
        depot_end_index = routing.End(0)
        time_dim.CumulVar(depot_start_index).SetRange(int(depot_open_s), int(depot_close_s))
        time_dim.CumulVar(depot_end_index).SetRange(int(depot_open_s), int(depot_close_s))

        # Ventana por parada.
        for k, s in enumerate(stops, 1):
            if s.ventana_inicio is None or s.ventana_fin is None:
                continue
            idx = manager.NodeToIndex(k)
            ini = max(int(s.ventana_inicio), int(depot_open_s))
            fin = min(int(s.ventana_fin), int(depot_close_s))
            if ini > fin:
                logger.warning("Ventana del cliente {} colapsada tras intersección con jornada",
                               s.cliente_id)
                fin = ini
            time_dim.CumulVar(idx).SetRange(ini, fin)
    else:
        # MVP 4: dimensión Time relativa, sólo para limitar la jornada total.
        routing.AddDimension(
            transit_cb_idx, 0, int(max_route_time_s), True, "Time",
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

    # ---- (MVP 7) ocupación dinámica con pickup-delivery ----
    # net_demand[k] = -vol_entregado_k + ratio*vol_retornable_k.
    # Inicio depot = vol_total_l (camión lleno). Capacidad = cap_vol.
    if use_pickup_delivery:
        ratio_ret = config.RATIO_RETORNO_DEFECTO
        net = [0] + [
            -int(round(s.volumen_l)) + int(round(ratio_ret * s.volumen_retornable_l))
            for s in stops
        ]
        load_initial = int(round(sum(s.volumen_l for s in stops)))

        def net_demand_cb(from_idx: int) -> int:
            return net[manager.IndexToNode(from_idx)]
        net_cb_idx = routing.RegisterUnaryTransitCallback(net_demand_cb)

        # Slack = capacidad permite acumular hasta cap_vol al alza desde 0.
        # Usamos AddDimension (no AddDimensionWithVehicleCapacity) para permitir
        # cumul inicial en `load_initial`.
        routing.AddDimension(
            net_cb_idx,
            int(cap_vol),                # slack: hasta cap_vol de holgura
            int(cap_vol),                # capacidad máxima (max cumul)
            False,                        # no forzar inicio en 0
            "Ocupacion",
        )
        occ_dim = routing.GetDimensionOrDie("Ocupacion")
        # Ocupación inicial = vol_total cargado en el depot.
        depot_start = routing.Start(0)
        occ_dim.CumulVar(depot_start).SetRange(load_initial, load_initial)

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
            raw_solver_output={"or_status": or_status, "label": status_label,
                               "reason": "no_solution_within_time_limit_or_infeasible"},
        )

    # ---- Extraer ruta y métricas ----
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

    return Solution(
        ordered_stops=ordered,
        total_time_s=int(total_time),
        total_distance_m=int(total_dist),
        status="OPTIMAL" if status_label in ("OPTIMAL", "ROUTING_SUCCESS") else status_label,
        raw_solver_output=raw_out,
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

    import json as _json
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
        mats_raw = getattr(r, "materiales_json", "[]")
        try:
            mats = _json.loads(mats_raw) if isinstance(mats_raw, str) else list(mats_raw or [])
        except (ValueError, TypeError):
            mats = []
        stops.append(Stop(
            cliente_id=int(r.cliente_id),
            lat=float(lat), lng=float(lng),
            volumen_l=float(r.volumen_total_l),
            peso_kg=float(r.peso_total_kg),
            volumen_retornable_l=float(r.volumen_retornable_l),
            tiempo_servicio_s=t_serv,
            cliente_nombre=str(r.cliente_nombre),
            poblacion=str(r.poblacion),
            cp=str(getattr(r, "cp", "")),
            zona_dd=str(getattr(r, "zona_dd", "")),
            entrega_id=int(r.entrega_id),
            materiales=mats,
        ))
    if skipped:
        logger.warning("Transporte {}: {} clientes sin geocoding (saltados)",
                       transporte_id, skipped)
    return stops


# ---------------------------------------------------------------------------
# Ventanas horarias
# ---------------------------------------------------------------------------

def attach_time_windows(
    stops: list[Stop],
    fecha,                                        # datetime.date or pandas.Timestamp
    canonical: pd.DataFrame | None = None,
    horarios: pd.DataFrame | None = None,
) -> list[Stop]:
    """Puebla `Stop.ventana_inicio/fin` para los clientes con horario conocido.

    Devuelve la MISMA lista de stops mutada in-place (también la retorna por
    ergonomía). Loggea el % de match.
    """
    if hasattr(fecha, "date"):           # pandas Timestamp
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
    use_time_windows: bool = False,
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
    arrivals = sol.raw_solver_output.get("arrivals_s", [])
    print(f"\nOrden propuesto (cliente_id | nombre | población | volumen_l | hora llegada):")
    for k, s in enumerate(sol.ordered_stops, 1):
        win = ""
        if s.ventana_inicio is not None and s.ventana_fin is not None:
            win = f" [{_format_hms(s.ventana_inicio)}–{_format_hms(s.ventana_fin)}]"
        arr = f" llega {_format_hms(arrivals[k-1])}" if k - 1 < len(arrivals) else ""
        print(f"  {k:2d}. {s.cliente_id} | {s.cliente_nombre[:28]:28s} | "
              f"{s.poblacion[:16]:16s} | {s.volumen_l:7.1f} L{arr}{win}")

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
    parser.add_argument("--time-windows", action="store_true",
                        help="Activa ventanas horarias (Horarios_Entrega)")
    args = parser.parse_args()
    run_for_transporte(args.transport, truck=args.truck,
                       time_limit_s=args.time_limit,
                       use_time_windows=args.time_windows)


if __name__ == "__main__":
    _main()
