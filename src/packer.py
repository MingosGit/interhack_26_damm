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
    zona_dd: str = ""


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
                    capacidad_l: float = 2400.0) -> BayItem:
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
        zona_dd=getattr(stop, "zona_dd", ""),
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
    """Dos items pueden compartir bahía si comparten CP o zona logística."""
    if a.cp and b.cp and a.cp == b.cp:
        return True
    if a.zona_dd and b.zona_dd and a.zona_dd == b.zona_dd:
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

    def _fit_in_existing_bay(item: BayItem) -> int | None:
        """Encaja en la bahía abierta más reciente que tenga sitio.

        Preserva la localidad de descarga: los últimos clientes meten al
        fondo del camión, los primeros quedan en bahías de cabeza.
        """
        for k in range(min(next_bay, n_bays) - 1, -1, -1):
            b = bays[k]
            if (b.vol_usado_l + item.volumen_l <= b.capacidad_l
                    and b.peso_kg + item.peso_kg <= b.capacidad_kg_max):
                return k
        return None

    i = 0
    while i < len(ordered_stops):
        stop = ordered_stops[i]
        # Caso 1 — combinar dos pequeños consecutivos del mismo CP/zona.
        if (strategy == "hybrid" and stop.volumen_l < 0.5 * cap_vol_bay
                and i + 1 < len(ordered_stops) and next_bay < n_bays):
            nxt = ordered_stops[i + 1]
            if (nxt.volumen_l < 0.5 * cap_vol_bay
                    and stop.volumen_l + nxt.volumen_l <= cap_vol_bay
                    and stop.peso_kg + nxt.peso_kg <= cap_kg_bay):
                a = _build_bay_item(stop, capacidad_l=cap_vol_bay)
                b = _build_bay_item(nxt, capacidad_l=cap_vol_bay)
                if _shareable(a, b):
                    bays[next_bay].items.extend([a, b])
                    next_bay += 1
                    i += 2
                    continue

        n_required = max(1, int(_ceil_div(stop.volumen_l, cap_vol_bay)))

        # Caso 2 — quedan bahías nuevas para este cliente.
        if next_bay + n_required <= n_bays:
            if n_required == 1:
                bays[next_bay].items.append(_build_bay_item(stop, capacidad_l=cap_vol_bay))
                next_bay += 1
            else:
                vol_per_bay = stop.volumen_l / n_required
                peso_per_bay = stop.peso_kg / n_required
                mats = _materiales_from_stop(stop)
                for k in range(n_required):
                    share_mats = mats if k == 0 else []
                    bays[next_bay + k].items.append(_build_bay_item(
                        stop, materiales=share_mats,
                        volumen_l=vol_per_bay, peso_kg=peso_per_bay,
                        capacidad_l=cap_vol_bay,
                    ))
                next_bay += n_required
            i += 1
            continue

        # Caso 3 — N paradas > N bahías: encaja en la última con espacio.
        item = _build_bay_item(stop, capacidad_l=cap_vol_bay)
        slot = _fit_in_existing_bay(item)
        if slot is None:
            raise VolumenExcedidoError(
                f"No cabe el cliente {stop.cliente_id} en ninguna bahía existente"
            )
        bays[slot].items.append(item)
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
# Visualización 3D (plotly)
# ---------------------------------------------------------------------------

def _color_for_client(cid: int) -> str:
    palette = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
               "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
               "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
               "#c49c94", "#f7b6d2", "#c7c7c7", "#dbdb8d", "#9edae5"]
    return palette[hash(cid) % len(palette)]


def _box_mesh(x0: float, y0: float, z0: float,
              dx: float, dy: float, dz: float,
              color: str, name: str, hover: str):
    """Devuelve un go.Mesh3d que dibuja un cubo."""
    import plotly.graph_objects as go
    xs = [x0, x0 + dx, x0 + dx, x0, x0, x0 + dx, x0 + dx, x0]
    ys = [y0, y0, y0 + dy, y0 + dy, y0, y0, y0 + dy, y0 + dy]
    zs = [z0, z0, z0, z0, z0 + dz, z0 + dz, z0 + dz, z0 + dz]
    # 12 triángulos (6 caras × 2)
    i = [7, 0, 0, 0, 4, 4, 6, 6, 4, 0, 3, 2]
    j = [3, 4, 1, 2, 5, 6, 5, 2, 0, 1, 6, 3]
    k = [0, 7, 2, 3, 6, 7, 1, 1, 5, 5, 7, 6]
    return go.Mesh3d(
        x=xs, y=ys, z=zs, i=i, j=j, k=k,
        color=color, opacity=0.6, name=name,
        hovertext=hover, hoverinfo="text", showscale=False,
    )


def to_3d_visualization(load: TruckLoad, *, save_to: str | None = None):
    """Render isométrico del camión con bahías apiladas por cliente.

    Si ``save_to`` se pasa, escribe el HTML; de lo contrario devuelve la
    `plotly.graph_objects.Figure`.
    """
    import plotly.graph_objects as go

    n_bays = len(load.bays)
    bay_l = config.BAHIA_LARGO_CM
    bay_w = config.BAHIA_ANCHO_CM
    bay_h = config.BAHIA_ALTO_CM

    traces = []
    # Outline del camión.
    truck_outline = _box_mesh(
        0, 0, 0, n_bays * bay_l, bay_w, bay_h,
        color="rgba(180,180,180,0.05)",
        name=f"Camión {load.truck_type}",
        hover=f"Camión {load.truck_type} | {load.vol_total_l:.0f}L total",
    )
    traces.append(truck_outline)

    # Items por bahía: stack vertical proporcional a volumen.
    for b in load.bays:
        x0 = b.index * bay_l
        z_cursor = 0.0
        for it in b.items:
            # Altura del item proporcional a vol respecto a capacidad bahía.
            item_vol_ratio = it.volumen_l / b.capacidad_l if b.capacidad_l else 0
            dz = max(5, item_vol_ratio * bay_h)
            color = _color_for_client(it.cliente_id)
            hover = (f"Cliente {it.cliente_id}<br>{it.cliente_nombre}<br>"
                     f"{it.poblacion} {it.cp}<br>"
                     f"{it.volumen_l:.1f} L | {it.peso_kg:.1f} kg<br>"
                     f"tipo: {it.tipo_dominante}<br>"
                     f"bahía {b.index} (parada {b.index + 1})")
            traces.append(_box_mesh(
                x0, 0, z_cursor, bay_l, bay_w, dz,
                color=color, name=f"{it.cliente_nombre[:20]} (b{b.index})",
                hover=hover,
            ))
            z_cursor += dz

        # Línea de suelo entre bahías para separar visualmente.
        traces.append(go.Scatter3d(
            x=[x0, x0, x0, x0, x0],
            y=[0, bay_w, bay_w, 0, 0],
            z=[0, 0, bay_h, bay_h, 0],
            mode="lines", line=dict(color="rgba(80,80,80,0.4)", width=2),
            showlegend=False, hoverinfo="skip",
        ))

    # Flecha indicando lado de descarga.
    traces.append(go.Scatter3d(
        x=[0, 0], y=[bay_w + 30, bay_w + 30], z=[bay_h / 2, bay_h / 2],
        mode="text", text=["▶ lado descarga (parada 1)"],
        textfont=dict(size=14, color="red"),
        showlegend=False, hoverinfo="skip",
    ))

    fig = go.Figure(data=traces)
    fig.update_layout(
        title=f"Carga camión {load.truck_type} | "
              f"{load.vol_total_l:.0f}L · {load.peso_total_kg:.0f}kg · "
              f"coherencia={load.coherencia_cliente:.2f}",
        scene=dict(
            xaxis_title="Longitudinal (cm) · bahías 0→N",
            yaxis_title="Ancho (cm)",
            zaxis_title="Alto (cm)",
            aspectmode="data",
            camera=dict(eye=dict(x=1.6, y=-1.4, z=0.9)),
        ),
        margin=dict(l=0, r=0, t=40, b=0),
    )
    if save_to:
        fig.write_html(save_to)
    return fig
