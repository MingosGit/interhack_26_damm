"""Logística inversa: estimar volumen de retornos por parada y modelar
la evolución temporal de la ocupación del camión.

Modelo MVP 7:
- En cada parada `k`, el chófer ENTREGA `vol_entregado_k` y RECOGE
  `vol_retornado_k`. La ocupación tras la parada k es:
      ocupacion(k) = ocupacion(k-1) − vol_entregado_k + vol_retornado_k
- Restricción operativa: `ocupacion(k)` nunca puede superar la capacidad
  del camión (no se puede recoger más de lo que cabe).
- El KPI clave es ``% retornables recogidos`` = vol_recogido / vol_recoger
  esperado. La meta es > 90%.

El módulo NO lanza el solver; expone:
- ``estimate_returns_per_stop`` para cada Stop, derivado del % retornable
  conocido del producto entregado y un ratio configurable.
- ``temporal_volume_profile`` para la traza de ocupación.
- ``returns_kpi`` para calcular el KPI agregado.

El acoplamiento al VRP se hace en `vrp_solver.solve_single_truck` mediante
el flag ``use_pickup_delivery=True``: usa una dimensión adicional para
asegurar que la ocupación no exceda la capacidad en ningún momento.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from loguru import logger

from src import config
from src.vrp_solver import Stop


# ---------------------------------------------------------------------------
# Estimación de retornos por parada
# ---------------------------------------------------------------------------

def estimate_returns_per_stop(
    stop: Stop,
    ratio: float = config.RATIO_RETORNO_DEFECTO,
) -> float:
    """Volumen de retornos previstos en una parada (litros).

    Heurística: se asume que el cliente devuelve `ratio` × volumen retornable
    entregado (es decir, los envases del producto entregado en visitas
    previas). Si `volumen_retornable_l` no está poblado, se asume 0.
    """
    base = float(getattr(stop, "volumen_retornable_l", 0.0) or 0.0)
    return max(0.0, ratio * base)


# ---------------------------------------------------------------------------
# Perfil temporal de ocupación
# ---------------------------------------------------------------------------

@dataclass
class OcupacionPunto:
    parada_idx: int            # 0 = depot inicial, 1..N = paradas
    cliente_id: int | None
    cliente_nombre: str
    vol_entregado_l: float
    vol_retornado_l: float
    ocupacion_l: float          # tras la operación de esta parada
    espacio_libre_l: float


@dataclass
class TemporalProfile:
    puntos: list[OcupacionPunto]
    capacidad_l: float
    vol_inicial_l: float        # carga al salir del depot
    vol_retornado_total_l: float
    vol_entregado_total_l: float

    @property
    def ocupacion_max_l(self) -> float:
        return max((p.ocupacion_l for p in self.puntos), default=0.0)

    @property
    def excede_capacidad(self) -> bool:
        return self.ocupacion_max_l > self.capacidad_l + 1e-6


def temporal_volume_profile(
    ordered_stops: list[Stop],
    truck_capacity_l: float,
    *,
    return_ratio: float = config.RATIO_RETORNO_DEFECTO,
) -> TemporalProfile:
    """Calcula la ocupación del camión paso a paso.

    Asume que el camión sale del depot con `vol_inicial = sum(vol_entregado)`.
    En cada parada se descuenta lo entregado y se suman los retornos.
    """
    vol_inicial = sum(s.volumen_l for s in ordered_stops)
    pts = [OcupacionPunto(
        parada_idx=0,
        cliente_id=None,
        cliente_nombre="DEPOT (salida)",
        vol_entregado_l=0.0,
        vol_retornado_l=0.0,
        ocupacion_l=vol_inicial,
        espacio_libre_l=truck_capacity_l - vol_inicial,
    )]
    occ = vol_inicial
    total_ent = 0.0
    total_ret = 0.0
    for k, s in enumerate(ordered_stops, 1):
        ent = float(s.volumen_l)
        ret = estimate_returns_per_stop(s, ratio=return_ratio)
        occ = occ - ent + ret
        total_ent += ent
        total_ret += ret
        pts.append(OcupacionPunto(
            parada_idx=k,
            cliente_id=int(s.cliente_id),
            cliente_nombre=s.cliente_nombre,
            vol_entregado_l=ent,
            vol_retornado_l=ret,
            ocupacion_l=round(occ, 2),
            espacio_libre_l=round(truck_capacity_l - occ, 2),
        ))
    return TemporalProfile(
        puntos=pts,
        capacidad_l=float(truck_capacity_l),
        vol_inicial_l=vol_inicial,
        vol_retornado_total_l=total_ret,
        vol_entregado_total_l=total_ent,
    )


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

@dataclass
class ReturnsKPI:
    vol_retorno_estimado_l: float
    vol_recogido_l: float
    pct_recogido: float
    paradas_con_retornos: int
    paradas_capacity_violation: int


def returns_kpi(profile: TemporalProfile,
                ordered_stops: list[Stop],
                ratio: float = config.RATIO_RETORNO_DEFECTO) -> ReturnsKPI:
    """KPI: % retornables recogidos sobre lo esperado."""
    estimado = sum(estimate_returns_per_stop(s, ratio=ratio) for s in ordered_stops)
    recogido = profile.vol_retornado_total_l
    pct = (recogido / estimado) if estimado > 1e-6 else 1.0
    n_violations = sum(1 for p in profile.puntos
                       if p.ocupacion_l > profile.capacidad_l + 1e-6)
    n_with_returns = sum(1 for p in profile.puntos if p.vol_retornado_l > 0)
    return ReturnsKPI(
        vol_retorno_estimado_l=round(estimado, 2),
        vol_recogido_l=round(recogido, 2),
        pct_recogido=round(pct, 4),
        paradas_con_retornos=n_with_returns,
        paradas_capacity_violation=n_violations,
    )


# ---------------------------------------------------------------------------
# Visualización plotly: área apilada outbound + returns
# ---------------------------------------------------------------------------

def plot_temporal_profile(profile: TemporalProfile, *, save_to: str | None = None):
    """Devuelve (o guarda como HTML) un gráfico plotly con dos series:
    volumen aún por entregar (outbound, decreciente) y volumen retornado
    (creciente). Línea horizontal = capacidad.
    """
    import plotly.graph_objects as go

    xs = [p.parada_idx for p in profile.puntos]
    labels = [p.cliente_nombre[:24] for p in profile.puntos]

    # Reconstruir outbound restante en cada paso
    outbound_remaining = []
    returned_cum = []
    out_acc = profile.vol_inicial_l
    ret_acc = 0.0
    for p in profile.puntos:
        out_acc -= p.vol_entregado_l
        ret_acc += p.vol_retornado_l
        outbound_remaining.append(round(max(out_acc, 0.0), 2))
        returned_cum.append(round(ret_acc, 2))

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=xs, y=outbound_remaining, name="Outbound restante (L)",
        mode="lines+markers", stackgroup="one", fill="tonexty",
        line=dict(color="#1f77b4"), text=labels, hovertemplate="%{text}: %{y:.0f} L",
    ))
    fig.add_trace(go.Scatter(
        x=xs, y=returned_cum, name="Retornos acumulados (L)",
        mode="lines+markers", stackgroup="one", fill="tonexty",
        line=dict(color="#ff7f0e"), text=labels, hovertemplate="%{text}: %{y:.0f} L",
    ))
    fig.add_hline(y=profile.capacidad_l, line_dash="dash", line_color="red",
                  annotation_text="Capacidad camión", annotation_position="top right")

    fig.update_layout(
        title=("Ocupación del camión a lo largo de la ruta · "
               f"recogidos {profile.vol_retornado_total_l:.0f} / "
               f"entregados {profile.vol_entregado_total_l:.0f} L"),
        xaxis_title="Parada (0 = depot)",
        yaxis_title="Volumen (L)",
        margin=dict(l=40, r=20, t=50, b=40),
        legend=dict(orientation="h", y=1.05),
    )
    if save_to:
        fig.write_html(save_to)
    return fig
