"""KPIs y comparación baseline (orden real) vs optimizado (OR-Tools).

Métricas del MVP 8:
    - distancia total (m)
    - tiempo total (s) — incluye servicio
    - n_movimientos_descarga (heurística)
    - % retornables recogidos

`n_movimientos_descarga`:
    - Real: `Σ_clientes n_materiales × 1.5`. La heurística captura que con
      carga por referencia el chófer hace movimientos extra para alcanzar
      cada producto.
    - Optimizado: `Σ_clientes n_materiales × 1.0`. Acceso directo a la bahía
      del cliente.
    - Δ esperado ≈ −33 %.

Uso CLI:
    python -m src.kpis compare --transport 11561535
    python -m src.kpis batch [--limit N] [--time-limit 5]
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from loguru import logger

from src import (
    config,
    distance_matrix,
    reverse_logistics as rl,
    vrp_solver,
)
from src.exceptions import DammSmartTruckError


# Heurísticas
_REAL_FACTOR_DISPERSION = 1.5      # carga por referencia → más movimientos
_OPT_FACTOR_DISPERSION = 1.0       # carga por bahía → 1 acceso por material
_REAL_PCT_RETORNABLES_BASELINE = 0.75   # sin gestión de bahías inversa


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------

@dataclass
class KPIComparison:
    transporte_id: int
    fecha: date
    n_paradas: int

    real_distancia_m: int
    real_tiempo_s: int
    real_n_movimientos_descarga: int
    real_pct_retornables_recogidos: float

    opt_distancia_m: int
    opt_tiempo_s: int
    opt_n_movimientos_descarga: int
    opt_pct_retornables_recogidos: float

    delta_distancia_pct: float
    delta_tiempo_pct: float
    delta_movimientos_pct: float
    delta_retornables_pp: float       # puntos porcentuales (no %)

    status: str = "OK"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["fecha"] = self.fecha.isoformat() if isinstance(self.fecha, (date, datetime)) else str(self.fecha)
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _movimientos_descarga(stops: list, factor: float) -> int:
    """Σ n_materiales por cliente × factor de dispersión."""
    total = 0.0
    for s in stops:
        n = len(getattr(s, "materiales", []) or [])
        if n == 0:
            n = 1
        total += n * factor
    return int(round(total))


def _pct_change(opt: float, base: float) -> float:
    if abs(base) < 1e-9:
        return 0.0
    return round(100.0 * (opt - base) / base, 4)


# ---------------------------------------------------------------------------
# Comparación de un transporte
# ---------------------------------------------------------------------------

def compare(
    transporte_id: int,
    *,
    truck: str = "6P",
    time_limit_s: int = 5,
    canonical: pd.DataFrame | None = None,
    geocoding: pd.DataFrame | None = None,
) -> KPIComparison:
    """Compara el orden real (baseline, sort by entrega_id) con la solución
    de OR-Tools para `transporte_id`.

    `canonical` y `geocoding` son opcionales para permitir reutilizar lectura
    en `batch_compare`.
    """
    if canonical is None:
        canonical = pd.read_parquet(config.CANONICAL_PARQUET)
    if geocoding is None:
        geocoding = pd.read_parquet(config.GEOCODING_PARQUET)

    sub = canonical[canonical["transporte"] == transporte_id]
    if sub.empty:
        raise DammSmartTruckError(f"Transporte {transporte_id} no existe")

    fecha_raw = sub["fecha"].iloc[0]
    fecha_val = fecha_raw.date() if hasattr(fecha_raw, "date") else fecha_raw

    stops = vrp_solver.build_stops_from_transporte(transporte_id, canonical, geocoding)
    if not stops:
        return KPIComparison(
            transporte_id=transporte_id, fecha=fecha_val, n_paradas=0,
            real_distancia_m=0, real_tiempo_s=0, real_n_movimientos_descarga=0,
            real_pct_retornables_recogidos=0.0,
            opt_distancia_m=0, opt_tiempo_s=0, opt_n_movimientos_descarga=0,
            opt_pct_retornables_recogidos=0.0,
            delta_distancia_pct=0.0, delta_tiempo_pct=0.0,
            delta_movimientos_pct=0.0, delta_retornables_pp=0.0,
            status="NO_STOPS_GEOCODED",
        )

    coords = [(config.DEPOT_LAT, config.DEPOT_LNG)] + [(s.lat, s.lng) for s in stops]
    time_mat, dist_mat = distance_matrix.get_matrix(coords)

    cap_l = config.TRUCKS[truck]["vol_m3"] * 1000.0
    cap_kg = float(config.TRUCKS[truck]["peso_max_kg"])

    base_t, base_d = vrp_solver.baseline_metrics(stops, time_mat, dist_mat)

    # Caso trivial: 1 parada → no hay reordenación posible.
    if len(stops) <= 1:
        n_mov_real = _movimientos_descarga(stops, _REAL_FACTOR_DISPERSION)
        n_mov_opt = _movimientos_descarga(stops, _OPT_FACTOR_DISPERSION)
        return KPIComparison(
            transporte_id=transporte_id, fecha=fecha_val, n_paradas=len(stops),
            real_distancia_m=base_d, real_tiempo_s=base_t,
            real_n_movimientos_descarga=n_mov_real,
            real_pct_retornables_recogidos=_REAL_PCT_RETORNABLES_BASELINE,
            opt_distancia_m=base_d, opt_tiempo_s=base_t,
            opt_n_movimientos_descarga=n_mov_opt,
            opt_pct_retornables_recogidos=1.0,
            delta_distancia_pct=0.0, delta_tiempo_pct=0.0,
            delta_movimientos_pct=_pct_change(n_mov_opt, n_mov_real),
            delta_retornables_pp=round(100 * (1.0 - _REAL_PCT_RETORNABLES_BASELINE), 2),
            status="TRIVIAL",
        )

    sol = vrp_solver.solve_single_truck(
        stops, (config.DEPOT_LAT, config.DEPOT_LNG),
        cap_l, cap_kg, time_mat, dist_mat,
        time_limit_s=time_limit_s,
    )
    if sol.status not in ("OPTIMAL", "FEASIBLE", "ROUTING_SUCCESS"):
        logger.warning("Transporte {}: solver devolvió {}", transporte_id, sol.status)

    n_mov_real = _movimientos_descarga(stops, _REAL_FACTOR_DISPERSION)
    n_mov_opt = _movimientos_descarga(sol.ordered_stops, _OPT_FACTOR_DISPERSION)

    profile = rl.temporal_volume_profile(sol.ordered_stops, cap_l)
    kpi_ret = rl.returns_kpi(profile, sol.ordered_stops)
    opt_pct_ret = float(min(1.0, kpi_ret.pct_recogido))

    return KPIComparison(
        transporte_id=transporte_id, fecha=fecha_val, n_paradas=len(stops),
        real_distancia_m=base_d, real_tiempo_s=base_t,
        real_n_movimientos_descarga=n_mov_real,
        real_pct_retornables_recogidos=_REAL_PCT_RETORNABLES_BASELINE,
        opt_distancia_m=sol.total_distance_m, opt_tiempo_s=sol.total_time_s,
        opt_n_movimientos_descarga=n_mov_opt,
        opt_pct_retornables_recogidos=opt_pct_ret,
        delta_distancia_pct=_pct_change(sol.total_distance_m, base_d),
        delta_tiempo_pct=_pct_change(sol.total_time_s, base_t),
        delta_movimientos_pct=_pct_change(n_mov_opt, n_mov_real),
        delta_retornables_pp=round(100 * (opt_pct_ret - _REAL_PCT_RETORNABLES_BASELINE), 2),
        status=sol.status,
    )


# ---------------------------------------------------------------------------
# Batch compare
# ---------------------------------------------------------------------------

def batch_compare(
    fecha_inicio: date | None = None,
    fecha_fin: date | None = None,
    *,
    truck: str = "6P",
    time_limit_s: int = 3,
    min_stops: int = 2,
    limit: int | None = None,
    persist_csv: Path | None = None,
) -> pd.DataFrame:
    """Aplica `compare` a todos los transportes en el rango y devuelve un
    DataFrame con una fila por transporte. Persiste a CSV si `persist_csv`.

    Sólo se procesan transportes con al menos `min_stops` paradas geocodificadas.
    """
    canonical = pd.read_parquet(config.CANONICAL_PARQUET)
    geocoding = pd.read_parquet(config.GEOCODING_PARQUET)

    if fecha_inicio is not None:
        canonical = canonical[canonical["fecha"] >= pd.Timestamp(fecha_inicio)]
    if fecha_fin is not None:
        canonical = canonical[canonical["fecha"] <= pd.Timestamp(fecha_fin)]

    geo_ok = set(geocoding[geocoding["status"].astype(str)
                            .str.startswith("ok")]["cliente_id"])
    eligible = canonical[canonical["cliente_id"].isin(geo_ok)]
    counts = (eligible.groupby("transporte").size()
              .rename("n_geocoded").reset_index())
    transports = counts[counts["n_geocoded"] >= min_stops]["transporte"].tolist()
    if limit:
        transports = transports[:limit]

    logger.info("batch_compare: {} transportes elegibles (min_stops={}, limit={})",
                len(transports), min_stops, limit)

    rows: list[dict] = []
    for k, tid in enumerate(transports, 1):
        try:
            kpi = compare(tid, truck=truck, time_limit_s=time_limit_s,
                          canonical=canonical, geocoding=geocoding)
        except Exception as exc:                       # noqa: BLE001
            logger.error("Transporte {} fallo: {}", tid, exc)
            continue
        rows.append(kpi.to_dict())
        if k % 25 == 0 or k == len(transports):
            logger.info("Progreso {}/{}", k, len(transports))

    df = pd.DataFrame(rows)

    if persist_csv:
        persist_csv = Path(persist_csv)
        persist_csv.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(persist_csv, index=False)
        logger.info("Persistido {}", persist_csv)
    return df


# ---------------------------------------------------------------------------
# Reporting / plots
# ---------------------------------------------------------------------------

def aggregate_means(df: pd.DataFrame) -> dict[str, float]:
    """Promedios agregados sobre el batch (excluye trivials/no-stops)."""
    if df.empty:
        return {}
    real = df[df["status"].isin(("OPTIMAL", "FEASIBLE", "ROUTING_SUCCESS"))]
    if real.empty:
        real = df
    return {
        "n_transportes": len(real),
        "delta_distancia_pct_mean": round(real["delta_distancia_pct"].mean(), 2),
        "delta_tiempo_pct_mean": round(real["delta_tiempo_pct"].mean(), 2),
        "delta_movimientos_pct_mean": round(real["delta_movimientos_pct"].mean(), 2),
        "delta_retornables_pp_mean": round(real["delta_retornables_pp"].mean(), 2),
        "n_outliers_distancia_worse": int((real["delta_distancia_pct"] > 0).sum()),
        "n_outliers_tiempo_worse": int((real["delta_tiempo_pct"] > 0).sum()),
    }


def plot_summary(df: pd.DataFrame, save_to: Path | None = None):
    """Histograma de mejoras por transporte (4 paneles).

    Marca outliers (Δ > 0 = empeora) en rojo.
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    fig = make_subplots(rows=2, cols=2, subplot_titles=(
        "Δ distancia (%)", "Δ tiempo (%)",
        "Δ movimientos descarga (%)", "Δ retornables recogidos (puntos pp)",
    ))

    def _hist(values, row, col, color="#1f77b4"):
        fig.add_trace(go.Histogram(
            x=values, nbinsx=40, marker_color=color, showlegend=False,
        ), row=row, col=col)
        fig.add_vline(x=0, line_dash="dash", line_color="red", row=row, col=col)

    _hist(df["delta_distancia_pct"], 1, 1)
    _hist(df["delta_tiempo_pct"], 1, 2)
    _hist(df["delta_movimientos_pct"], 2, 1, color="#2ca02c")
    _hist(df["delta_retornables_pp"], 2, 2, color="#9467bd")

    means = aggregate_means(df)
    title = (
        f"KPIs sobre {means.get('n_transportes', 0)} transportes · "
        f"Δdist {means.get('delta_distancia_pct_mean', 0):+.1f}% · "
        f"Δtiempo {means.get('delta_tiempo_pct_mean', 0):+.1f}% · "
        f"Δmov {means.get('delta_movimientos_pct_mean', 0):+.1f}% · "
        f"Δret +{means.get('delta_retornables_pp_mean', 0):.1f}pp"
    )
    fig.update_layout(title=title, height=600,
                      margin=dict(l=40, r=20, t=80, b=40))
    if save_to:
        fig.write_html(str(save_to))
    return fig


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_one = sub.add_parser("compare", help="Compara un transporte concreto")
    p_one.add_argument("--transport", type=int, required=True)
    p_one.add_argument("--truck", default="6P", choices=list(config.TRUCKS.keys()))
    p_one.add_argument("--time-limit", type=int, default=10)

    p_batch = sub.add_parser("batch", help="Compara todos los transportes")
    p_batch.add_argument("--start", type=str, default=None,
                         help="Fecha inicio YYYY-MM-DD")
    p_batch.add_argument("--end", type=str, default=None,
                         help="Fecha fin YYYY-MM-DD")
    p_batch.add_argument("--truck", default="6P", choices=list(config.TRUCKS.keys()))
    p_batch.add_argument("--time-limit", type=int, default=3)
    p_batch.add_argument("--min-stops", type=int, default=2)
    p_batch.add_argument("--limit", type=int, default=None)
    p_batch.add_argument("--no-csv", action="store_true")
    p_batch.add_argument("--no-plot", action="store_true")

    args = parser.parse_args()

    if args.cmd == "compare":
        kpi = compare(args.transport, truck=args.truck,
                      time_limit_s=args.time_limit)
        print()
        for k, v in kpi.to_dict().items():
            print(f"  {k:<35} {v}")
        return

    if args.cmd == "batch":
        start = date.fromisoformat(args.start) if args.start else None
        end = date.fromisoformat(args.end) if args.end else None
        csv_path = None if args.no_csv else config.KPI_COMPARISON_CSV
        df = batch_compare(
            fecha_inicio=start, fecha_fin=end,
            truck=args.truck, time_limit_s=args.time_limit,
            min_stops=args.min_stops, limit=args.limit,
            persist_csv=csv_path,
        )
        means = aggregate_means(df)
        print("\n=== Promedios agregados ===")
        for k, v in means.items():
            print(f"  {k:<35} {v}")
        if not args.no_plot:
            plot_summary(df, save_to=config.KPI_HISTOGRAM_HTML)
            print(f"\nHistograma → {config.KPI_HISTOGRAM_HTML}")


if __name__ == "__main__":
    _main()
