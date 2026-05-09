"""Tests del packer por bahias (MVP 6)."""
from __future__ import annotations

import pytest

from src import config, packer
from src.exceptions import VolumenExcedidoError
from src.vrp_solver import Stop


def _cap_bay_vol_l(truck: str = "6P") -> float:
    spec = config.TRUCKS[truck]
    return spec["vol_m3"] * 1000.0 / int(spec["palets"])


def _cap_bay_kg(truck: str = "6P") -> float:
    spec = config.TRUCKS[truck]
    return float(spec["peso_max_kg"]) / int(spec["palets"])


def _stop(cid: int, vol_l: float, peso_kg: float,
          *, cp: str = "", zona_dd: str = "") -> Stop:
    return Stop(
        cliente_id=cid,
        lat=0.0,
        lng=0.0,
        volumen_l=vol_l,
        peso_kg=peso_kg,
        tiempo_servicio_s=0,
        cliente_nombre=f"C{cid}",
        poblacion="",
        cp=cp,
        zona_dd=zona_dd,
    )


def test_pack_truck_six_clients_one_per_bay():
    cap_vol = _cap_bay_vol_l("6P")
    cap_kg = _cap_bay_kg("6P")
    stops = [
        _stop(1, cap_vol * 0.6, cap_kg * 0.1, cp="08001"),
        _stop(2, cap_vol * 0.6, cap_kg * 0.1, cp="08002"),
        _stop(3, cap_vol * 0.6, cap_kg * 0.1, cp="08003"),
        _stop(4, cap_vol * 0.6, cap_kg * 0.1, cp="08004"),
        _stop(5, cap_vol * 0.6, cap_kg * 0.1, cp="08005"),
        _stop(6, cap_vol * 0.6, cap_kg * 0.1, cp="08006"),
    ]

    load = packer.pack_truck(stops, truck_type="6P")
    assert load.coherencia_cliente == 1.0
    for idx, bay in enumerate(load.bays):
        assert len(bay.items) == 1
        assert bay.items[0].cliente_id == idx + 1


def test_pack_truck_large_client_spans_multiple_bays():
    cap_vol = _cap_bay_vol_l("6P")
    cap_kg = _cap_bay_kg("6P")
    stop = _stop(77, cap_vol * 2.6, cap_kg * 0.8, cp="08001")

    load = packer.pack_truck([stop], truck_type="6P")
    idxs = [b.index for b in load.bays if any(it.cliente_id == 77 for it in b.items)]
    assert idxs == [0, 1, 2]


def test_pack_truck_shares_bay_by_cp_or_zona():
    cap_vol = _cap_bay_vol_l("6P")
    cap_kg = _cap_bay_kg("6P")
    stops = [
        _stop(1, cap_vol * 0.3, cap_kg * 0.1, cp="08001"),
        _stop(2, cap_vol * 0.3, cap_kg * 0.1, cp="08001"),
        _stop(3, cap_vol * 0.3, cap_kg * 0.1, zona_dd="DD13100043"),
        _stop(4, cap_vol * 0.3, cap_kg * 0.1, zona_dd="DD13100043"),
    ]

    load = packer.pack_truck(stops, truck_type="6P")
    assert len(load.bays[0].items) == 2
    assert {it.cliente_id for it in load.bays[0].items} == {1, 2}
    assert len(load.bays[1].items) == 2
    assert {it.cliente_id for it in load.bays[1].items} == {3, 4}


def test_pack_truck_raises_when_over_capacity():
    cap_total = config.TRUCKS["6P"]["vol_m3"] * 1000.0
    stop = _stop(1, cap_total + 1.0, 100.0)

    with pytest.raises(VolumenExcedidoError):
        packer.pack_truck([stop], truck_type="6P")


def test_pack_truck_overflow_suggests_larger_truck():
    """6P (14400L) sobrepasado → mensaje sugiere 8P (19200L)."""
    cap_total = config.TRUCKS["6P"]["vol_m3"] * 1000.0
    stops = [_stop(1, cap_total + 100, 100.0)]
    with pytest.raises(VolumenExcedidoError) as exc:
        packer.pack_truck(stops, truck_type="6P")
    assert "8P" in str(exc.value)


def test_pack_truck_peso_overflow_raises():
    cap_kg_total = float(config.TRUCKS["6P"]["peso_max_kg"])
    stops = [_stop(1, 100.0, cap_kg_total + 1.0)]
    with pytest.raises(VolumenExcedidoError):
        packer.pack_truck(stops, truck_type="6P")


def test_first_stop_always_in_bay_0():
    """La bahía 0 (lado de descarga) DEBE contener al cliente de la 1ª parada."""
    cap_vol = _cap_bay_vol_l("6P")
    stops = [_stop(i, cap_vol * 0.4, 50.0) for i in range(1, 5)]
    load = packer.pack_truck(stops, truck_type="6P")
    assert 1 in load.bays[0].cliente_ids


def test_volumen_libre_post_descarga_is_monotonic_increasing():
    """A medida que se entrega, el espacio libre crece."""
    stops = [_stop(i, 500.0, 100.0) for i in range(1, 5)]
    load = packer.pack_truck(stops, truck_type="6P")
    libre = load.volumen_libre_post_descarga
    assert len(libre) == 4
    for k in range(1, len(libre)):
        assert libre[k] >= libre[k - 1]
    cap_total = config.TRUCKS["6P"]["vol_m3"] * 1000
    # tras la última parada, todo libre
    assert libre[-1] == pytest.approx(cap_total, abs=1e-6)


def test_stability_orders_barrels_below_caja_in_shared_bay():
    """En una bahía compartida, los barriles van debajo de las cajas."""
    cap_vol = _cap_bay_vol_l("6P")
    s_caja = Stop(
        cliente_id=1, lat=0, lng=0, volumen_l=cap_vol * 0.3, peso_kg=20,
        cliente_nombre="C1", cp="08001",
        materiales=[{"material": "M1", "uma": "CAJ", "vol_l": 200, "peso_kg": 20,
                     "denominacion": "CAJA", "cantidad": 1, "retornable": False}],
    )
    s_brl = Stop(
        cliente_id=2, lat=0, lng=0, volumen_l=cap_vol * 0.3, peso_kg=200,
        cliente_nombre="C2", cp="08001",
        materiales=[{"material": "M2", "uma": "BRL", "vol_l": 400, "peso_kg": 200,
                     "denominacion": "BARRIL", "cantidad": 1, "retornable": True}],
    )
    load = packer.pack_truck([s_caja, s_brl], "6P")
    # Comparten bahía 0 por mismo CP
    bay0 = load.bays[0]
    assert len(bay0.items) == 2
    # El BRL (cliente 2) debe estar primero (= abajo)
    assert bay0.items[0].tipo_dominante == "BARRIL"
    assert bay0.items[1].tipo_dominante == "CAJA"


def test_coherencia_cliente_meets_threshold_for_small_clients():
    """Criterio MPV: coherencia >= 0.85 para rutas con clientes pequeños."""
    stops = [_stop(i, 200.0, 50.0) for i in range(1, 7)]
    load = packer.pack_truck(stops, "6P")
    assert load.coherencia_cliente >= 0.85


def test_to_3d_visualization_returns_figure_with_traces():
    stops = [_stop(i, 200.0, 50.0) for i in range(1, 4)]
    load = packer.pack_truck(stops, "6P")
    fig = packer.to_3d_visualization(load)
    assert hasattr(fig, "data")
    assert len(fig.data) >= 4    # outline + items + separadores


def test_to_3d_visualization_writes_html(tmp_path):
    stops = [_stop(i, 200.0, 50.0) for i in range(1, 4)]
    load = packer.pack_truck(stops, "6P")
    out = tmp_path / "load.html"
    packer.to_3d_visualization(load, save_to=str(out))
    assert out.exists()
    assert "plotly" in out.read_text().lower()


def test_invalid_strategy_raises():
    stops = [_stop(1, 100.0, 50.0)]
    with pytest.raises(ValueError):
        packer.pack_truck(stops, "6P", strategy="random")


def test_invalid_truck_type_raises():
    stops = [_stop(1, 100.0, 50.0)]
    with pytest.raises(ValueError):
        packer.pack_truck(stops, "ZZ")


def test_truckload_aggregates_volumes_and_peso():
    stops = [_stop(1, 400.0, 200.0), _stop(2, 600.0, 300.0)]
    load = packer.pack_truck(stops, "6P")
    assert load.vol_total_l == pytest.approx(1000.0)
    assert load.peso_total_kg == pytest.approx(500.0)
    assert load.truck_type == "6P"
