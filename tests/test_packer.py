"""Tests del packer por bahías (MVP 6) y de la simulación de retornos (MVP 7)."""
from __future__ import annotations

import pytest

from src import config, packer
from src.exceptions import VolumenExcedidoError
from src.vrp_solver import Stop


# ---- helpers ----------------------------------------------------------------

def _stop(cid: int, vol: float = 800.0, ret: float = 0.0,
          peso: float = 50.0, poblacion: str = "X", cp: str = "08000",
          materiales: list[dict] | None = None) -> Stop:
    s = Stop(cliente_id=cid, lat=0.0, lng=0.0, volumen_l=vol,
             peso_kg=peso, volumen_retornable_l=ret,
             cliente_nombre=f"Cliente {cid}", poblacion=poblacion)
    # Atributos extra que el packer mira con getattr defensivo.
    setattr(s, "cp", cp)
    setattr(s, "materiales", materiales or [])
    return s


def _bay_cap_l() -> float:
    cap_vol_l, _, _ = packer._bay_capacity("6P")
    return cap_vol_l


# ---- pack_truck básico ------------------------------------------------------

def test_pack_truck_one_client_per_bay():
    """3 clientes pequeños, no comparten zona → 1 bahía cada uno."""
    bay_cap = _bay_cap_l()
    half = bay_cap * 0.4
    stops = [
        _stop(1, vol=half, poblacion="A", cp="08001"),
        _stop(2, vol=half, poblacion="B", cp="08002"),
        _stop(3, vol=half, poblacion="C", cp="08003"),
    ]
    load = packer.pack_truck(stops, truck_type="6P")
    assert load.truck_type == "6P"
    assert len(load.bays) == config.TRUCKS["6P"]["palets"]
    used_bays = [b for b in load.bays if b.items]
    assert len(used_bays) == 3
    # Cada bahía contiene un único cliente.
    for b in used_bays:
        assert len(b.cliente_ids) == 1


def test_pack_truck_overflow_raises():
    """Carga total > capacidad del 6P → VolumenExcedidoError."""
    spec = config.TRUCKS["6P"]
    cap_l = spec["vol_m3"] * 1000.0
    stops = [_stop(1, vol=cap_l + 100, peso=50)]
    with pytest.raises(VolumenExcedidoError):
        packer.pack_truck(stops, truck_type="6P")


def test_pack_truck_hybrid_combines_small_clients_same_zone():
    """Dos clientes pequeños del mismo CP comparten bahía en hybrid."""
    bay_cap = _bay_cap_l()
    small = bay_cap * 0.3
    stops = [
        _stop(1, vol=small, cp="08010", poblacion="MOLLET"),
        _stop(2, vol=small, cp="08010", poblacion="MOLLET"),
        _stop(3, vol=bay_cap * 0.7, cp="08020", poblacion="GRANOLLERS"),
    ]
    load = packer.pack_truck(stops, truck_type="6P", strategy="hybrid")
    # Bahía 0 con clientes 1 y 2 (compartida); bahía 1 con cliente 3.
    assert load.bays[0].cliente_ids == {1, 2}
    assert load.bays[1].cliente_ids == {3}


def test_pack_truck_large_client_uses_multiple_bays():
    """Un cliente con volumen > 1 bahía ocupa varias contiguas."""
    bay_cap = _bay_cap_l()
    stops = [_stop(1, vol=bay_cap * 2.5)]
    load = packer.pack_truck(stops, truck_type="6P")
    used = [b.index for b in load.bays if b.items]
    # Debería ocupar 3 bahías contiguas (ceil(2.5)=3).
    assert used == [0, 1, 2]


def test_pack_truck_coherencia_cliente_metric():
    """coherencia=1.0 cuando todos los clientes tienen sus bahías contiguas."""
    bay_cap = _bay_cap_l()
    stops = [_stop(i, vol=bay_cap * 0.6, cp=f"0801{i}", poblacion=f"P{i}")
             for i in range(1, 4)]
    load = packer.pack_truck(stops, truck_type="6P")
    assert load.coherencia_cliente == pytest.approx(1.0)


# ---- simulate_returns -------------------------------------------------------

def test_simulate_returns_no_overflow_basic():
    """Cada cliente recoge menos retornable que su volumen entregado:
    la propia bahía absorbe sus retornos sin overflow."""
    bay_cap = _bay_cap_l()
    stops = [
        _stop(1, vol=bay_cap * 0.8, ret=bay_cap * 0.4, cp="08001", poblacion="A"),
        _stop(2, vol=bay_cap * 0.8, ret=bay_cap * 0.4, cp="08002", poblacion="B"),
    ]
    load = packer.pack_truck(stops, truck_type="6P")
    sched = packer.simulate_returns(load)
    assert sched.feasible is True
    assert sched.overflow_total_l == 0.0
    # 1 evento por parada.
    assert len(sched.events) == 2
    # bays_post_route[0] = estado inicial = packing inicial (las dos bahías
    # llenas a 80%, resto vacío).
    assert sched.bays_post_route[0][0] == pytest.approx(bay_cap * 0.8)
    # Tras servir cliente 1: su bahía se libera y se rellena con su retornable.
    assert sched.bays_post_route[1][0] == pytest.approx(bay_cap * 0.4)
    # Total retornable acumulado al final = 2 × 0.4 × bay_cap.
    final_used = sum(sched.bays_post_route[-1])
    assert final_used == pytest.approx(2 * bay_cap * 0.4)


def test_simulate_returns_overflow_when_returns_too_big():
    """Cliente con retornable >> bahía propia y sin bahías libres → overflow."""
    bay_cap = _bay_cap_l()
    # Solo un cliente, ocupa 1 bahía, recoge mucho más de lo que cabe.
    stops = [_stop(1, vol=bay_cap * 0.9, ret=bay_cap * 5.0)]
    load = packer.pack_truck(stops, truck_type="6P")
    sched = packer.simulate_returns(load)
    # Aunque el camión completo (6 bahías) puede absorber bay_cap*5 si todas
    # las demás están libres, sólo bay 0 está usada y el resto vacías ya, así
    # que al volcar el retornable lo absorberán.
    assert sched.feasible is True
    # Pero si ahora usamos un camión donde TODAS las bahías están ocupadas,
    # no queda espacio libre y la recogida derrama.
    fur_cap_l, _, fur_n = packer._bay_capacity("FUR")
    full_stops = [_stop(i, vol=fur_cap_l * 0.95, ret=0.0,
                        cp=f"080{i:02d}", poblacion=f"P{i}")
                  for i in range(1, fur_n + 1)]
    # Ahora el primer cliente recoge mucho más de lo que cabe en su bahía.
    full_stops[0].volumen_retornable_l = fur_cap_l * (fur_n + 1)
    load2 = packer.pack_truck(full_stops, truck_type="FUR")
    sched2 = packer.simulate_returns(load2)
    assert sched2.feasible is False
    assert sched2.overflow_total_l > 0
    assert sched2.events[0].overflow_l > 0


def test_simulate_returns_zero_returns_keeps_bays_freed():
    """Sin retornables, las bahías quedan vacías tras descarga."""
    bay_cap = _bay_cap_l()
    stops = [
        _stop(1, vol=bay_cap * 0.7, ret=0.0, cp="08001", poblacion="A"),
        _stop(2, vol=bay_cap * 0.7, ret=0.0, cp="08002", poblacion="B"),
    ]
    load = packer.pack_truck(stops, truck_type="6P")
    sched = packer.simulate_returns(load)
    assert sched.feasible is True
    # Tras última parada todas las bahías deben estar vacías.
    assert sum(sched.bays_post_route[-1]) == pytest.approx(0.0)


def test_simulate_returns_pico_at_depot_when_returns_smaller_than_deliveries():
    """Si todos los pickups son menores que sus deliveries respectivos,
    la carga viva máxima es el inicio (sale lleno)."""
    bay_cap = _bay_cap_l()
    stops = [
        _stop(1, vol=bay_cap * 0.8, ret=bay_cap * 0.3),
        _stop(2, vol=bay_cap * 0.8, ret=bay_cap * 0.3),
        _stop(3, vol=bay_cap * 0.8, ret=bay_cap * 0.3),
    ]
    load = packer.pack_truck(stops, truck_type="6P")
    sched = packer.simulate_returns(load)
    assert sched.pico_parada_idx == 0
    assert sched.carga_viva_max_l == pytest.approx(3 * bay_cap * 0.8)


def test_simulate_returns_event_assigns_to_own_bay_first():
    """Verifica que el retornable del cliente k se intenta meter primero en
    las bahías que él acaba de liberar."""
    bay_cap = _bay_cap_l()
    stops = [
        _stop(1, vol=bay_cap * 0.9, ret=bay_cap * 0.5, cp="08001", poblacion="A"),
        _stop(2, vol=bay_cap * 0.9, ret=0.0, cp="08002", poblacion="B"),
    ]
    load = packer.pack_truck(stops, truck_type="6P")
    sched = packer.simulate_returns(load)
    ev1 = sched.events[0]
    assert ev1.cliente_id == 1
    # Asignación: cliente 1 ocupaba bahía 0 → su retornable va a bahía 0.
    bahias_destino = [bidx for bidx, _ in ev1.asignaciones]
    assert bahias_destino[0] == 0
