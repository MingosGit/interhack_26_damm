"""Tests del módulo reverse_logistics + extensión solver MVP 7."""
from __future__ import annotations

import numpy as np
import pytest

from src import config, reverse_logistics as rl, vrp_solver as vrp
from src.vrp_solver import Stop


def _stop(cid: int, vol: float, ret: float = 0.0, peso: float = None) -> Stop:
    return Stop(
        cliente_id=cid, lat=0, lng=0,
        volumen_l=vol, peso_kg=peso if peso is not None else vol * 0.5,
        volumen_retornable_l=ret, tiempo_servicio_s=0,
    )


# ---- estimate_returns_per_stop ---------------------------------------------

def test_estimate_returns_zero_when_no_retornable():
    s = _stop(1, vol=100, ret=0)
    assert rl.estimate_returns_per_stop(s) == 0.0


def test_estimate_returns_proportional_to_retornable_and_ratio():
    s = _stop(1, vol=100, ret=50)
    # Ratio defecto = 0.6 → 30 L
    assert rl.estimate_returns_per_stop(s) == pytest.approx(30.0)
    # Custom ratio
    assert rl.estimate_returns_per_stop(s, ratio=0.4) == pytest.approx(20.0)


# ---- temporal_volume_profile ----------------------------------------------

def test_profile_starts_with_full_truck_and_decreases_then_increases():
    """El camión sale lleno, baja al entregar, sube al recoger."""
    stops = [_stop(1, vol=300, ret=200), _stop(2, vol=200, ret=0)]
    cap = 14400.0
    profile = rl.temporal_volume_profile(stops, truck_capacity_l=cap)
    # 3 puntos: depot + 2 paradas
    assert len(profile.puntos) == 3
    assert profile.puntos[0].ocupacion_l == pytest.approx(500.0)  # vol_inicial
    # tras parada 1: -300 + 0.6*200 = -180 → 320
    assert profile.puntos[1].ocupacion_l == pytest.approx(320.0)
    # tras parada 2: -200 + 0 = -200 → 120
    assert profile.puntos[2].ocupacion_l == pytest.approx(120.0)
    assert profile.vol_entregado_total_l == pytest.approx(500.0)
    assert profile.vol_retornado_total_l == pytest.approx(120.0)
    assert not profile.excede_capacidad


def test_profile_detects_capacity_violation():
    """Si los retornos exceden el espacio liberado, ocupación supera cap."""
    stops = [_stop(1, vol=10, ret=200)]   # entrega 10 L pero recoge 120 L
    cap = 50.0
    # Carga inicial = 10. Tras parada: 10 - 10 + 120 = 120 > 50.
    profile = rl.temporal_volume_profile(stops, truck_capacity_l=cap)
    assert profile.excede_capacidad
    assert profile.ocupacion_max_l > cap


# ---- returns_kpi -----------------------------------------------------------

def test_returns_kpi_full_recovery():
    stops = [_stop(1, vol=100, ret=50), _stop(2, vol=200, ret=100)]
    profile = rl.temporal_volume_profile(stops, truck_capacity_l=14400.0)
    kpi = rl.returns_kpi(profile, stops)
    # Estimado = 0.6 * (50+100) = 90 L. Recogido = mismo (sin restricción).
    assert kpi.vol_retorno_estimado_l == pytest.approx(90.0)
    assert kpi.vol_recogido_l == pytest.approx(90.0)
    assert kpi.pct_recogido == pytest.approx(1.0)
    assert kpi.paradas_con_retornos == 2
    assert kpi.paradas_capacity_violation == 0


def test_returns_kpi_no_returns_means_full_pct():
    """Si no hay nada que recoger, % = 100% por convención."""
    stops = [_stop(1, vol=100, ret=0)]
    profile = rl.temporal_volume_profile(stops, truck_capacity_l=14400.0)
    kpi = rl.returns_kpi(profile, stops)
    assert kpi.pct_recogido == pytest.approx(1.0)


# ---- plot_temporal_profile -------------------------------------------------

def test_plot_temporal_profile_returns_figure():
    stops = [_stop(i, vol=100, ret=50) for i in range(1, 4)]
    profile = rl.temporal_volume_profile(stops, truck_capacity_l=1000.0)
    fig = rl.plot_temporal_profile(profile)
    assert hasattr(fig, "data")
    assert len(fig.data) >= 2   # outbound + retornos


def test_plot_temporal_profile_writes_html(tmp_path):
    stops = [_stop(i, vol=100, ret=50) for i in range(1, 4)]
    profile = rl.temporal_volume_profile(stops, truck_capacity_l=1000.0)
    out = tmp_path / "profile.html"
    rl.plot_temporal_profile(profile, save_to=str(out))
    assert out.exists()
    assert "plotly" in out.read_text().lower()


# ---- Solver con use_pickup_delivery ----------------------------------------

def _identity_matrices(n, value=60):
    t = np.full((n, n), value, dtype=np.int64); np.fill_diagonal(t, 0)
    d = np.full((n, n), value * 10, dtype=np.int64); np.fill_diagonal(d, 0)
    return t, d


def test_solve_with_pickup_delivery_returns_solution():
    """Con pickup_delivery activo, el solver respeta capacidad acumulada."""
    stops = [
        Stop(cliente_id=1, lat=0, lng=0, volumen_l=200, peso_kg=80,
             volumen_retornable_l=100, tiempo_servicio_s=0),
        Stop(cliente_id=2, lat=0, lng=0, volumen_l=300, peso_kg=120,
             volumen_retornable_l=50,  tiempo_servicio_s=0),
        Stop(cliente_id=3, lat=0, lng=0, volumen_l=100, peso_kg=40,
             volumen_retornable_l=0,   tiempo_servicio_s=0),
    ]
    t, d = _identity_matrices(4, value=120)
    sol = vrp.solve_single_truck(
        stops, (0, 0), 14400.0, 6500.0, t, d,
        use_pickup_delivery=True, time_limit_s=10,
    )
    assert sol.status in ("OPTIMAL", "FEASIBLE", "ROUTING_SUCCESS")
    assert len(sol.ordered_stops) == 3


def test_solve_with_pickup_delivery_capacity_tight_route_still_feasible():
    """Camión justo: sale lleno, va liberando, retornos caben en lo liberado."""
    cap = 600.0
    stops = [
        Stop(cliente_id=i, lat=0, lng=0, volumen_l=200, peso_kg=80,
             volumen_retornable_l=100, tiempo_servicio_s=0)
        for i in (1, 2, 3)
    ]
    # Inicial = 600 = cap. Tras parada: -200+60=-140 → 460. -200+60 → 320. -200+0 → 180.
    t, d = _identity_matrices(4, value=60)
    sol = vrp.solve_single_truck(
        stops, (0, 0), cap, 6500.0, t, d,
        use_pickup_delivery=True, time_limit_s=10,
    )
    assert sol.status in ("OPTIMAL", "FEASIBLE", "ROUTING_SUCCESS")
