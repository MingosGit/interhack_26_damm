"""
Módulo de Recomendaciones de Layout del Almacén.

Genera sugerencias accionables sobre cómo reorganizar el almacén DDI Mollet
para reducir tiempo de preparación de rutas, basándose en frecuencia y volumen
real de cada SKU en los transportes históricos.

Idea-fuerza: si un producto aparece en el 80% de las rutas, debería estar
pegado al muelle de carga. Si aparece en el 5%, puede ir al fondo.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json

import pandas as pd

from src import config


@dataclass
class WarehouseRecommendation:
    """Recomendación de ubicación para un material en el almacén."""
    material: str
    denominacion: str
    uma: str
    frecuencia_transportes: int       # nº de transportes donde aparece
    frecuencia_pct: float             # % sobre total transportes
    volumen_total_l: float            # vol acumulado en los datos
    zona_recomendada: str             # "muelle" | "intermedia" | "fondo"
    razon: str
    ahorro_estimado_min: float        # tiempo ganado por route si se mueve


_ZONE_BY_RANK_PCT: list[tuple[float, str, str]] = [
    (0.10, "muelle",      "🟢 Pegado al muelle de carga (top 10% más frecuente)"),
    (0.30, "intermedia",  "🟡 Zona intermedia (alto uso)"),
    (0.60, "media",       "🟠 Estantería estándar"),
    (1.00, "fondo",       "🔴 Fondo del almacén (uso esporádico)"),
]


def _zone_for_rank(rank_pct: float) -> tuple[str, str]:
    for threshold, zone, reason in _ZONE_BY_RANK_PCT:
        if rank_pct <= threshold:
            return zone, reason
    return "fondo", "Uso esporádico"


def _global_sku_frequency(canonical_path: Path | None = None) -> pd.DataFrame:
    """Frecuencia global de cada SKU sobre el dataset histórico completo."""
    path = canonical_path or config.CANONICAL_PARQUET
    if not path.exists():
        return pd.DataFrame(columns=["material", "denominacion", "uma",
                                     "n_transportes", "vol_total_l", "freq_pct"])

    df = pd.read_parquet(path)
    n_transportes_total = df["transporte"].nunique()
    if n_transportes_total == 0:
        return pd.DataFrame(columns=["material", "denominacion", "uma",
                                     "n_transportes", "vol_total_l", "freq_pct"])

    rows = []
    for _, row in df.iterrows():
        blob = row.get("materiales_json")
        if not blob:
            continue
        for m in json.loads(blob):
            rows.append({
                "transporte": row["transporte"],
                "material": str(m.get("material", "")),
                "denominacion": str(m.get("denominacion", "")),
                "uma": str(m.get("uma", "")),
                "vol_l": float(m.get("vol_l", 0) or 0),
            })

    if not rows:
        return pd.DataFrame(columns=["material", "denominacion", "uma",
                                     "n_transportes", "vol_total_l", "freq_pct"])

    flat = pd.DataFrame(rows)
    agg = (
        flat.groupby(["material", "denominacion", "uma"], as_index=False)
        .agg(
            n_transportes=("transporte", "nunique"),
            vol_total_l=("vol_l", "sum"),
        )
    )
    agg["freq_pct"] = agg["n_transportes"] / n_transportes_total
    return agg.sort_values(
        ["n_transportes", "vol_total_l"], ascending=False
    ).reset_index(drop=True)


def recommend_warehouse_layout(
    canonical_path: Path | None = None,
    top_k: int = 25,
) -> list[WarehouseRecommendation]:
    """Genera recomendaciones de zonificación para los top-K SKUs.

    El ahorro estimado por ruta es heurístico:
        - Mover del fondo al muelle ahorra ~1.5 min por línea de picking.
        - Entre intermedia y muelle, ~0.8 min.
    Se asume 1 línea de picking por aparición en transporte (cota inferior).
    """
    df = _global_sku_frequency(canonical_path)
    if df.empty:
        return []

    n = len(df)
    recommendations: list[WarehouseRecommendation] = []
    for idx, row in df.head(top_k).iterrows():
        rank_pct = (idx + 1) / n
        zone, reason = _zone_for_rank(rank_pct)
        # Ahorro: las top frecuentes ahorran más al estar cerca del muelle
        if zone == "muelle":
            ahorro = float(row["n_transportes"]) * 1.5
        elif zone == "intermedia":
            ahorro = float(row["n_transportes"]) * 0.8
        else:
            ahorro = 0.0
        recommendations.append(WarehouseRecommendation(
            material=str(row["material"]),
            denominacion=str(row["denominacion"]),
            uma=str(row["uma"]),
            frecuencia_transportes=int(row["n_transportes"]),
            frecuencia_pct=float(row["freq_pct"]),
            volumen_total_l=float(row["vol_total_l"]),
            zona_recomendada=zone,
            razon=reason,
            ahorro_estimado_min=ahorro,
        ))
    return recommendations


def picking_path_for_route(
    route_stops: list[dict],
    layout_recommendations: list[WarehouseRecommendation] | None = None,
) -> dict[str, Any]:
    """Para una ruta concreta, calcula el orden óptimo de picking siguiendo
    la zonificación recomendada (LIFO por cliente: último cliente primero).

    Devuelve métricas de eficiencia y la secuencia de picking.
    """
    if not route_stops:
        return {"steps": [], "total_lines": 0, "estimated_pick_time_min": 0.0}

    # Mapa material → zona recomendada (muelle más rápido, fondo más lento)
    zone_by_material: dict[str, str] = {}
    if layout_recommendations:
        for r in layout_recommendations:
            zone_by_material[r.material] = r.zona_recomendada

    zone_minutes = {
        "muelle": 0.4, "intermedia": 0.7, "media": 1.0, "fondo": 1.5,
    }

    steps = []
    total_lines = 0
    total_min = 0.0
    for stop in reversed(route_stops):  # LIFO: último cliente, primero al almacén
        for mat in stop.get("materiales", []) or []:
            mat_code = str(mat.get("material", ""))
            zone = zone_by_material.get(mat_code, "media")
            t = zone_minutes[zone]
            steps.append({
                "cliente": stop.get("cliente_nombre", ""),
                "material": mat_code,
                "denominacion": str(mat.get("denominacion", ""))[:40],
                "uma": mat.get("uma", ""),
                "cantidad": int(mat.get("cantidad", 0) or 0),
                "zona": zone,
                "tiempo_min": t,
            })
            total_lines += 1
            total_min += t

    return {
        "steps": steps,
        "total_lines": total_lines,
        "estimated_pick_time_min": round(total_min, 1),
        "avg_min_per_line": round(total_min / total_lines, 2) if total_lines else 0,
    }
