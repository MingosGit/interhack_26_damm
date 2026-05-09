"""Packer por bahías para los camiones de DDI con lonas laterales.

A diferencia del bin-packing 3D clásico (que asume LIFO desde puertas
traseras), aquí el camión es una secuencia de N **bahías longitudinales**
(una por palet) accesibles independientemente desde el lado. La bahía 0 es
la más cercana al lado de descarga del primer cliente.

Estrategia ``hybrid`` (la del MPV):
    1. Cada cliente se coloca en bahías contiguas en el orden de descarga
       (cliente 1 → bahía 0, cliente 2 → bahía siguiente que quede libre).
    2. Si un cliente excede 1 bahía, ocupa varias contiguas.
    3. Dos clientes pequeños (< 0.5 bahía) consecutivos pueden compartir
       bahía si comparten zona/CP — refuerza la coherencia de descarga.
    4. Dentro de cada bahía, los items se ordenan por estabilidad: barriles
       y packs pesados abajo, cajas en medio, frágiles arriba.
    5. Si el volumen total supera la capacidad → ``VolumenExcedidoError``
       con sugerencia de camión mayor.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable

from loguru import logger

from src import config
from src.exceptions import VolumenExcedidoError
from src.vrp_solver import Stop


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------

# Prioridad de estabilidad por UMA: cuanto MENOR el número, más abajo se carga.
_STABILITY_RANK = {
    "BRL": 0,    # barril → suelo
    "BID": 0,
    "PAK": 1,    # packs de agua, latas
    "BOT": 2,    # botellas
    "CAJ": 3,    # cajas estándar
    "PQ":  4,    # paquetes
    "EST": 5,    # estuches
    "TIR": 5,
    "TB":  5,
    "UN":  6,
    "ZPR": 7,
}

_TIPO_DOMINANTE_BY_UMA = {
    "BRL": "BARRIL", "BID": "BARRIL",
    "BOT": "BOTELLA",
    "CAJ": "CAJA",
    "PAK": "PACK", "TB": "PACK",
    "EST": "ESTUCHE", "TIR": "ESTUCHE", "PQ": "ESTUCHE",
    "UN": "UNIDAD", "ZPR": "UNIDAD",
}


@dataclass
class BayItem:
    cliente_id: int
    cliente_nombre: str
    materiales: list[dict]
    volumen_l: float
    peso_kg: float
    altura_estimada_cm: float
    tipo_dominante: str
    es_retornable: bool = False     # True si representa retornos recogidos
    poblacion: str = ""
    cp: str = ""


@dataclass
class Bay:
    index: int
    items: list[BayItem]
    capacidad_l: float
    capacidad_kg_max: float

    @property
    def vol_usado_l(self) -> float:
        return sum(it.volumen_l for it in self.items)

    @property
    def peso_kg(self) -> float:
        return sum(it.peso_kg for it in self.items)

    @property
    def espacio_libre_l(self) -> float:
        return max(0.0, self.capacidad_l - self.vol_usado_l)

    @property
    def cliente_ids(self) -> set[int]:
        return {it.cliente_id for it in self.items}


@dataclass
class TruckLoad:
    bays: list[Bay]
    truck_type: str
    vol_total_l: float
    peso_total_kg: float
    volumen_libre_post_descarga: list[float] = field(default_factory=list)
    coherencia_cliente: float = 1.0
    ordered_stops: list[Stop] = field(default_factory=list)


# ---------------------------------------------------------------------------
# MVP 7 — Logística inversa: simulación de retornos sobre la carga
# ---------------------------------------------------------------------------

@dataclass
class ReturnEvent:
    """Un evento de recogida de retornable en una parada."""
    parada_idx: int          # 1-based en la ruta
    cliente_id: int
    volumen_l: float         # volumen retornable que TIENE este cliente
    asignaciones: list[tuple[int, float]] = field(default_factory=list)
    """Lista de (bahia_idx, vol_asignado_l). Suma debe ser ≤ volumen_l."""
    overflow_l: float = 0.0  # volumen que no cupo (caso patológico)


@dataclass
class ReturnSchedule:
    """Cronograma de retornos a lo largo de la ruta.

    `bays_post_route[k]` es una lista paralela a las bahías con el volumen
    OCUPADO en cada bahía justo después de servir la parada k (1-indexed,
    ``k=0`` = camión recién salido).
    """
    events: list[ReturnEvent]
    bays_post_route: list[list[float]]
    overflow_total_l: float
    feasible: bool                            # True si overflow_total_l == 0
    capacidad_total_l: float
    carga_viva_max_l: float
    pico_parada_idx: int


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _bay_capacity(truck_type: str) -> tuple[float, float, int]:
    spec = config.TRUCKS[truck_type]
    n = int(spec["palets"])
    cap_vol_l = spec["vol_m3"] * 1000.0 / n
    cap_kg = float(spec["peso_max_kg"]) / n
    return cap_vol_l, cap_kg, n


def _materiales_from_stop(stop: Stop) -> list[dict]:
    """Decodifica `materiales_json` del canonical (lista de dicts)."""
    raw = getattr(stop, "materiales", None)
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str):
        return json.loads(raw)
    return []


def _tipo_dominante(materiales: list[dict]) -> str:
    if not materiales:
        return "MIXTO"
    counts: dict[str, float] = {}
    for m in materiales:
        tipo = _TIPO_DOMINANTE_BY_UMA.get(str(m.get("uma", "")).upper(), "MIXTO")
        counts[tipo] = counts.get(tipo, 0.0) + float(m.get("vol_l", 0.0))
    if not counts:
        return "MIXTO"
    top, top_vol = max(counts.items(), key=lambda kv: kv[1])
    total = sum(counts.values())
    if total > 0 and top_vol / total >= 0.6:
        return top
    return "MIXTO"


def _estimate_altura(volumen_l: float, capacidad_l: float) -> float:
    """Aproxima la altura ocupada en cm asumiendo footprint estándar."""
    if capacidad_l <= 0:
        return 0.0
    ratio = min(1.0, volumen_l / capacidad_l)
    return float(round(ratio * config.BAHIA_ALTO_CM, 1))


def _build_bay_item(stop: Stop, materiales: list[dict] | None = None,
                    volumen_l: float | None = None,
                    peso_kg: float | None = None,
                    capacidad_l: float = config.BAHIA_LARGO_CM) -> BayItem:
    mats = materiales if materiales is not None else _materiales_from_stop(stop)
    v = float(volumen_l if volumen_l is not None else stop.volumen_l)
    p = float(peso_kg if peso_kg is not None else stop.peso_kg)
    return BayItem(
        cliente_id=stop.cliente_id,
        cliente_nombre=stop.cliente_nombre,
        materiales=mats,
        volumen_l=v,
        peso_kg=p,
        altura_estimada_cm=_estimate_altura(v, capacidad_l),
        tipo_dominante=_tipo_dominante(mats),
        poblacion=stop.poblacion,
        cp=getattr(stop, "cp", ""),
    )


def _sort_items_by_stability(items: list[BayItem]) -> list[BayItem]:
    """Mete primero (= abajo) los items más estables/pesados."""
    def rank(it: BayItem) -> tuple[int, float]:
        # Si el item tiene un único material, usamos su UMA. Si hay varios,
        # usamos el tipo_dominante mapeado a un rango representativo.
        if len(it.materiales) == 1:
            uma = str(it.materiales[0].get("uma", "")).upper()
            return (_STABILITY_RANK.get(uma, 4), -it.peso_kg)
        # Tipo_dominante → UMA representativa
        rep = {"BARRIL": "BRL", "BOTELLA": "BOT", "CAJA": "CAJ",
               "PACK": "PAK", "ESTUCHE": "EST", "UNIDAD": "UN"}.get(
            it.tipo_dominante, "CAJ")
        return (_STABILITY_RANK.get(rep, 4), -it.peso_kg)
    return sorted(items, key=rank)


# ---------------------------------------------------------------------------
# pack_truck
# ---------------------------------------------------------------------------

def _shareable(a: BayItem, b: BayItem) -> bool:
    """Dos items pueden compartir bahía si comparten CP o población."""
    if a.cp and b.cp and a.cp == b.cp:
        return True
    if a.poblacion and b.poblacion and a.poblacion == b.poblacion:
        return True
    return False


def pack_truck(
    ordered_stops: list[Stop],
    truck_type: str = "6P",
    *,
    strategy: str = "hybrid",
) -> TruckLoad:
    """Asigna paradas a bahías siguiendo `strategy`.

    Devuelve `TruckLoad`. Si el volumen total supera la capacidad del camión,
    lanza ``VolumenExcedidoError`` con sugerencia de camión mayor.
    """
    if strategy not in ("hybrid", "by_client", "by_reference"):
        raise ValueError(f"Strategy desconocida: {strategy}")
    if truck_type not in config.TRUCKS:
        raise ValueError(f"Truck desconocido: {truck_type}")

    cap_vol_bay, cap_kg_bay, n_bays = _bay_capacity(truck_type)
    cap_vol_total = cap_vol_bay * n_bays
    cap_kg_total = cap_kg_bay * n_bays

    total_vol = sum(s.volumen_l for s in ordered_stops)
    total_peso = sum(s.peso_kg for s in ordered_stops)
    if total_vol > cap_vol_total or total_peso > cap_kg_total:
        sugerido = _suggest_larger_truck(truck_type, total_vol, total_peso)
        raise VolumenExcedidoError(
            f"Carga {total_vol:.1f}L/{total_peso:.1f}kg excede "
            f"camión {truck_type} ({cap_vol_total:.0f}L/{cap_kg_total:.0f}kg). "
            f"Sugerencia: {sugerido or '-- ningún camión disponible es suficiente --'}"
        )

    bays: list[Bay] = [
        Bay(index=i, items=[], capacidad_l=cap_vol_bay,
            capacidad_kg_max=cap_kg_bay)
        for i in range(n_bays)
    ]
    next_bay = 0

    i = 0
    while i < len(ordered_stops):
        stop = ordered_stops[i]
        # Si esa parada cabe en < 0.5 bahía Y el siguiente cliente es pequeño Y
        # comparten zona, los combinamos en la misma bahía.
        if (strategy == "hybrid" and stop.volumen_l < 0.5 * cap_vol_bay
                and i + 1 < len(ordered_stops)):
            nxt = ordered_stops[i + 1]
            if (nxt.volumen_l < 0.5 * cap_vol_bay
                    and stop.volumen_l + nxt.volumen_l <= cap_vol_bay
                    and stop.peso_kg + nxt.peso_kg <= cap_kg_bay):
                a = _build_bay_item(stop, capacidad_l=cap_vol_bay)
                b = _build_bay_item(nxt, capacidad_l=cap_vol_bay)
                if _shareable(a, b):
                    if next_bay >= n_bays:
                        raise VolumenExcedidoError(
                            f"No quedan bahías para {stop.cliente_id}/{nxt.cliente_id}"
                        )
                    bays[next_bay].items.extend([a, b])
                    next_bay += 1
                    i += 2
                    continue

        n_required = max(1, int(_ceil_div(stop.volumen_l, cap_vol_bay)))
        if next_bay + n_required > n_bays:
            raise VolumenExcedidoError(
                f"Cliente {stop.cliente_id} requiere {n_required} bahías, "
                f"sólo quedan {n_bays - next_bay}"
            )
        if n_required == 1:
            bays[next_bay].items.append(_build_bay_item(stop, capacidad_l=cap_vol_bay))
            next_bay += 1
        else:
            # Repartir uniformemente en bahías contiguas.
            vol_per_bay = stop.volumen_l / n_required
            peso_per_bay = stop.peso_kg / n_required
            mats = _materiales_from_stop(stop)
            for k in range(n_required):
                share_mats = mats if k == 0 else []   # detalle sólo en la primera
                bays[next_bay + k].items.append(_build_bay_item(
                    stop, materiales=share_mats,
                    volumen_l=vol_per_bay, peso_kg=peso_per_bay,
                    capacidad_l=cap_vol_bay,
                ))
            next_bay += n_required
        i += 1

    # Ordenar items dentro de cada bahía por estabilidad.
    for b in bays:
        b.items = _sort_items_by_stability(b.items)

    coherencia = _coherencia_cliente(bays)
    libre_post = _ocupacion_post_descarga(ordered_stops, total_vol, cap_vol_total)
    load = TruckLoad(
        bays=bays,
        truck_type=truck_type,
        vol_total_l=total_vol,
        peso_total_kg=total_peso,
        volumen_libre_post_descarga=libre_post,
        coherencia_cliente=coherencia,
        ordered_stops=list(ordered_stops),
    )
    logger.info(
        "Pack {} → {} bahías ({:.0f}L / {:.0f}kg, coherencia={:.2f}, ocupación {:.0f}%)",
        truck_type, n_bays, total_vol, total_peso, coherencia,
        100 * total_vol / cap_vol_total,
    )
    return load


def _ceil_div(num: float, den: float) -> int:
    if den <= 0:
        return 1
    return int(-(-num // den))


def _coherencia_cliente(bays: list[Bay]) -> float:
    """1 si todos los items de cada cliente están en bahías contiguas."""
    by_client: dict[int, list[int]] = {}
    for b in bays:
        for it in b.items:
            by_client.setdefault(it.cliente_id, []).append(b.index)
    if not by_client:
        return 1.0
    scores = []
    for cid, idxs in by_client.items():
        idxs_sorted = sorted(idxs)
        contiguos = all(idxs_sorted[k + 1] - idxs_sorted[k] == 1
                        for k in range(len(idxs_sorted) - 1))
        scores.append(1.0 if contiguos else 0.0)
    return float(sum(scores) / len(scores))


def _ocupacion_post_descarga(
    stops: list[Stop], total_vol: float, cap_total: float,
) -> list[float]:
    """Devuelve la lista de espacio libre tras cada parada (entrega).

    Modelo: el camión sale lleno con `total_vol` y libera el volumen entregado
    en cada parada (sin contar retornos para MVP 6).
    """
    libre = []
    used = total_vol
    for s in stops:
        used = max(0.0, used - s.volumen_l)
        libre.append(round(cap_total - used, 1))
    return libre


def _suggest_larger_truck(current: str, vol_l: float, peso_kg: float) -> str | None:
    """Devuelve el camión más pequeño que sí cabría, o None."""
    for t, spec in sorted(config.TRUCKS.items(),
                           key=lambda kv: kv[1]["vol_m3"]):
        if t == current:
            continue
        if spec["vol_m3"] * 1000.0 >= vol_l and spec["peso_max_kg"] >= peso_kg:
            return t
    return None


# ---------------------------------------------------------------------------
# MVP 7 — simulate_returns
# ---------------------------------------------------------------------------

def simulate_returns(load: TruckLoad) -> ReturnSchedule:
    """Simula entrega + recogida de retornables a lo largo de la ruta.

    Modelo simple (decidido para el MVP):

    1. Las paradas se atienden en el orden de ``load.ordered_stops``.
    2. Al servir el cliente k:
        a. Las bahías que contienen items de k se vacían (descarga).
        b. Los retornables del cliente k (``stop.volumen_retornable_l``) se
           meten **preferentemente en las bahías que él acaba de liberar**.
        c. Si sobra, se distribuyen en orden de bahía hacia bahías ya libres
           (de clientes servidos antes).
        d. Si tras agotar todas las libres aún queda retornable → ``overflow``
           (la solución no cabe físicamente con el packing actual).

    Devuelve un ``ReturnSchedule`` con el cronograma + métrica de pico.
    """
    n_bays = len(load.bays)
    if n_bays == 0:
        return ReturnSchedule(
            events=[], bays_post_route=[],
            overflow_total_l=0.0, feasible=True,
            capacidad_total_l=0.0,
            carga_viva_max_l=0.0, pico_parada_idx=0,
        )

    # Estado: volumen ocupado por bahía. Empieza con el packing inicial.
    bay_used: list[float] = [b.vol_usado_l for b in load.bays]
    bay_cap: list[float] = [b.capacidad_l for b in load.bays]
    cap_total = sum(bay_cap)

    # Para cada cliente, qué bahías contienen items suyos (en orden).
    bays_of_client: dict[int, list[int]] = {}
    for b in load.bays:
        for cid in b.cliente_ids:
            bays_of_client.setdefault(int(cid), []).append(b.index)
    for cid in bays_of_client:
        bays_of_client[cid].sort()

    events: list[ReturnEvent] = []
    bays_post_route: list[list[float]] = [list(bay_used)]
    overflow_total = 0.0
    pico = float(sum(bay_used))
    pico_idx = 0

    served: set[int] = set()
    for k, stop in enumerate(load.ordered_stops, 1):
        cid = int(stop.cliente_id)

        # (a) Descarga: las bahías de este cliente quedan libres.
        own_bays = bays_of_client.get(cid, [])
        for bidx in own_bays:
            bay_used[bidx] = 0.0
        served.add(cid)

        # (b)+(c) Recogida del retornable: primero a sus propias bahías,
        # luego a bahías de clientes ya servidos.
        ret_remaining = float(stop.volumen_retornable_l)
        ev = ReturnEvent(parada_idx=k, cliente_id=cid, volumen_l=ret_remaining)

        order: list[int] = list(own_bays) + [
            bidx for bidx in range(n_bays)
            if bidx not in own_bays
            and any(int(it.cliente_id) in served for it in load.bays[bidx].items)
            and bay_used[bidx] == 0.0          # libre
        ]
        # Por compatibilidad, también permitimos derramar a cualquier bahía
        # que esté actualmente libre (aunque no sea de servidos), con menor
        # prioridad. Esto refleja que las lonas laterales dan 