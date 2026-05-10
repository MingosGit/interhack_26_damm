"""
Módulo de Visualización de Carga
Crea representaciones visuales (ASCII, JSON y Plotly 2D) de cómo quedará cargado el camión.

Visualiza:
- Planta del camión con distribución de palets
- Secuencia de carga desde almacén
- Zonas de acceso (lateral, trasero)
- Apilamiento seguro (barriles abajo, cajas arriba)
"""
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from pathlib import Path
import re


# Una línea sólo es una RECOGIDA real (vacío que vuelve al depósito) si su
# descripción contiene una de estas palabras. El flag `retornable` del ETL
# original es demasiado amplio: marca cualquier botella/barril/caja con envase
# reutilizable, incluso cuando es una entrega de producto LLENO.
_RETURN_RX = re.compile(
    r"(VAC[IÍ]OS?|RETORNO|RETORNOS|DEVOLUC[IÍ]ON|RECOGIDA)",
    re.IGNORECASE,
)


def is_actual_return(material_name: str = "", denominacion: str = "") -> bool:
    """True sólo cuando la línea es físicamente un envase vacío que vuelve."""
    return bool(_RETURN_RX.search(f"{material_name} {denominacion}"))


@dataclass
class LoadingZone:
    """Representa una zona de carga en el camión."""
    zone_id: str  # 'left', 'center', 'right', 'top'
    zone_name: str
    width: int  # Caracteres de ancho en ASCII
    height: int  # Líneas de alto en ASCII
    max_volume_l: int
    access_type: str  # 'lateral', 'trasero', 'superior'
    items: list[dict] = None
    
    def __post_init__(self):
        if self.items is None:
            self.items = []


_ROW_FULL_NAME = {"n": "norte", "s": "sur", "m": "centro"}
_ROW_LABEL = {"n": "N", "s": "S", "m": "M"}


def truck_zone_layout(truck_code: str, truck_capacity_l: int) -> dict:
    """Devuelve la geometría de zonas de un camión.

    8P  → 2 filas (N/S) × 4 columnas + 2 toldos
    6P  → 2 filas (N/S) × 3 columnas + 2 toldos
    FUR → 1 fila (centro)  × 3 columnas, SIN toldos (es furgoneta)
    """
    code = (truck_code or "").upper().strip()
    if code == "8P":
        n_rows, n_cols, has_toldos = 2, 4, True
    elif code == "6P":
        n_rows, n_cols, has_toldos = 2, 3, True
    elif code in ("FUR", "3P", "FURGON", "FURGONETA"):
        n_rows, n_cols, has_toldos = 1, 3, False
    else:
        n_rows, n_cols, has_toldos = 2, 3, True  # fallback razonable (6P)

    n_bays = n_rows * n_cols
    bay_capacity_l = float(truck_capacity_l) / n_bays if n_bays else 0.0
    # Toldo lateral ≈ 1/6 del volumen total por lado (~16%); dos toldos ≈ 33%
    toldo_capacity_l = (float(truck_capacity_l) / 6.0) if has_toldos else 0.0
    row_codes = ["n", "s"] if n_rows == 2 else ["m"]

    return {
        "truck_code": code or "?",
        "n_rows": n_rows,
        "n_cols": n_cols,
        "n_bays": n_bays,
        "row_codes": row_codes,
        "bay_capacity_l": bay_capacity_l,
        "has_toldos": has_toldos,
        "toldo_capacity_l": toldo_capacity_l,
    }


def _build_zones_for_truck(layout: dict) -> tuple[dict, list[str]]:
    """Crea el dict de LoadingZone y la lista ordenada de bay_ids para el camión."""
    zones: dict[str, LoadingZone] = {}
    bay_ids: list[str] = []
    bay_cap = int(round(layout["bay_capacity_l"]))
    for rcode in layout["row_codes"]:
        full = _ROW_FULL_NAME[rcode]
        for c in range(1, layout["n_cols"] + 1):
            zid = f"bay_{rcode}{c}"
            bay_ids.append(zid)
            zones[zid] = LoadingZone(
                zone_id=zid,
                zone_name=f"BAHIA {_ROW_LABEL[rcode]}-{c} (palet {c} de {layout['n_cols']}, lado {full})",
                width=20, height=4,
                max_volume_l=bay_cap,
                access_type=f"trasero/lateral_{full}",
            )
    if layout["has_toldos"]:
        toldo_cap = int(round(layout["toldo_capacity_l"]))
        zones["toldo_izq"] = LoadingZone(
            zone_id="toldo_izq", zone_name="TOLDO LATERAL IZQUIERDO (retornables)",
            width=8, height=3, max_volume_l=toldo_cap,
            access_type="lateral_deslizable",
        )
        zones["toldo_der"] = LoadingZone(
            zone_id="toldo_der", zone_name="TOLDO LATERAL DERECHO (retornables)",
            width=8, height=3, max_volume_l=toldo_cap,
            access_type="lateral_deslizable",
        )
    return zones, bay_ids


def _split_piece(item: dict, chunk_vol: float, total_vol: float, suffix: str) -> dict:
    """Crea una copia de `item` con volumen/peso/cantidad escalados al chunk."""
    if total_vol <= 0:
        ratio = 0.0
    else:
        ratio = chunk_vol / total_vol
    piece = dict(item)
    piece['vol_l'] = chunk_vol
    piece['peso_kg'] = float(item.get('peso_kg', 0) or 0) * ratio
    cantidad = item.get('cantidad', 0) or 0
    piece['cantidad'] = max(1, int(round(cantidad * ratio))) if cantidad else 0
    if chunk_vol < total_vol - 1e-6:
        piece['name'] = f"{item['name']}{suffix}"
    return piece


def _pack_into_bays_safely(
    item: dict,
    bay_ids: list[str],
    bay_loads: dict[str, float],
    zones: dict,
    bay_cap: float,
) -> None:
    """Coloca un item en bahías sin superar la capacidad. Si el item es más
    grande que el espacio libre, se parte en varios trozos. Última parte si
    no queda sitio: irá a la bahía menos cargada (puede pasarse, marcado como
    overload — el caller debería notificarlo)."""
    remaining = float(item.get('vol_l', 0) or 0)
    total = remaining
    if remaining <= 0:
        return
    part_idx = 0
    while remaining > 1e-6:
        free_per_bay = {b: bay_cap - bay_loads[b] for b in bay_ids}
        target, free = max(free_per_bay.items(), key=lambda kv: kv[1])
        if free <= 1e-6:
            # Todo lleno: último recurso, ir a la menos cargada y aceptar overload
            target = min(bay_loads, key=bay_loads.get)
            chunk = remaining
        else:
            chunk = min(remaining, free)
        suffix = f" · parte {part_idx + 1}" if chunk < total - 1e-6 or part_idx > 0 else ""
        piece = _split_piece(item, chunk, total, suffix)
        zones[target].items.append(piece)
        bay_loads[target] += chunk
        remaining -= chunk
        part_idx += 1
        if free <= 1e-6:
            break  # safety


def _pack_into_toldos_safely(
    item: dict,
    toldo_loads: dict[str, float],
    zones: dict,
    toldo_cap: float,
) -> bool:
    """Mete el item en toldos sin superar capacidad, dividiéndolo si hace falta.
    Devuelve True si TODO el volumen cupo. Si no, devuelve False y deja el
    sobrante en `item` para que el caller lo redirija a bahías."""
    remaining = float(item.get('vol_l', 0) or 0)
    total = remaining
    if remaining <= 0:
        return True
    part_idx = 0
    while remaining > 1e-6:
        free_per_toldo = {t: toldo_cap - toldo_loads[t] for t in toldo_loads}
        target, free = max(free_per_toldo.items(), key=lambda kv: kv[1])
        if free <= 1e-6:
            # Toldos llenos: el resto vuelve al caller para ir a bahías.
            item['vol_l'] = remaining
            item['peso_kg'] = float(item.get('peso_kg', 0) or 0) * (remaining / total) if total > 0 else 0
            cant = item.get('cantidad', 0) or 0
            item['cantidad'] = max(1, int(round(cant * remaining / total))) if cant and total > 0 else cant
            if remaining < total - 1e-6:
                item['name'] = f"{item['name']} · resto"
            return False
        chunk = min(remaining, free)
        suffix = f" · parte {part_idx + 1}" if chunk < total - 1e-6 or part_idx > 0 else ""
        piece = _split_piece(item, chunk, total, suffix)
        zones[target].items.append(piece)
        toldo_loads[target] += chunk
        remaining -= chunk
        part_idx += 1
    return True


def visualize_loading_plan(
    stops: list[dict],
    truck_capacity_l: int,
    truck_code: str = "6P",
) -> dict[str, Any]:
    """Plan de carga visualizado, adaptado al tipo de camión.

    - 8P: 4 jaulas × 2 palets (N/S) + 2 toldos.
    - 6P: 3 jaulas × 2 palets (N/S) + 2 toldos.
    - FUR (3P): 3 jaulas × 1 palet, sin toldos (furgoneta).

    Los retornables van a los toldos hasta su capacidad real; lo que sobra se
    reparte en las bahías para no superar nunca el 100% de los toldos.
    """
    layout = truck_zone_layout(truck_code, truck_capacity_l)
    zones, bay_ids = _build_zones_for_truck(layout)
    has_toldos = layout["has_toldos"]
    toldo_cap = layout["toldo_capacity_l"]

    # ===== AGREGAR ITEMS POR MATERIAL+UMA =====
    all_items_by_uma = defaultdict(lambda: {
        'cantidad': 0, 'vol_l': 0, 'peso_kg': 0, 'stops': [],
        'denominacion': '', 'is_return': False,
    })

    for stop in stops:
        for mat in stop.get('materiales', []):
            uma = mat.get('uma', 'UNIT')
            mat_code = str(mat.get('material', '?'))
            mat_key = f"{mat_code} ({uma})"
            entry = all_items_by_uma[mat_key]
            entry['cantidad'] += mat.get('cantidad', 0)
            entry['vol_l'] += mat.get('vol_l', 0)
            entry['peso_kg'] += mat.get('peso_kg', 0)
            entry['stops'].append(stop['order'])
            denom = str(mat.get('denominacion', '') or '')
            if denom and not entry['denominacion']:
                entry['denominacion'] = denom
            # Recogida REAL (vacío que vuelve) — se detecta por descripción,
            # no por si el envase es retornable. Una botella de whisky en
            # envase retornable que se ENTREGA llena NO es una recogida.
            if is_actual_return(mat_code, denom):
                entry['is_return'] = True

    heavy_items: list[dict] = []
    light_items: list[dict] = []
    returnable_items: list[dict] = []

    uma_priority = {'BRL': 0, 'BID': 1, 'BOT': 2, 'CAJ': 3, 'UN': 4, 'TB': 4, 'EST': 3}

    for item_key, item_data in sorted(
        all_items_by_uma.items(),
        key=lambda x: uma_priority.get(x[0].split('(')[-1].rstrip(')'), 99),
    ):
        uma = item_key.split('(')[-1].rstrip(')')
        item_obj = {
            'name': item_key,
            'cantidad': item_data['cantidad'],
            'vol_l': item_data['vol_l'],
            'peso_kg': item_data['peso_kg'],
            'stops': item_data['stops'],
            'retornable': item_data['is_return'],
            'denominacion': item_data['denominacion'],
        }
        if item_data['is_return']:
            returnable_items.append(item_obj)
        elif uma in ('BRL', 'BID', 'BOT'):
            heavy_items.append(item_obj)
        else:
            light_items.append(item_obj)

    # ===== REPARTO EN BAHÍAS (entrega) — packing con tope de capacidad =====
    bay_loads = {bid: 0.0 for bid in bay_ids}
    bay_cap = layout["bay_capacity_l"]
    all_sorted = sorted(heavy_items + light_items, key=lambda x: x['vol_l'], reverse=True)
    for item in all_sorted:
        _pack_into_bays_safely(item, bay_ids, bay_loads, zones, bay_cap)

    # ===== REPARTO DE RECOGIDAS =====
    # Ordenamos las recogidas por volumen DESC para que las grandes encuentren
    # toldo libre antes de que se vaya llenando con piezas pequeñas.
    returnable_items.sort(key=lambda x: x['vol_l'], reverse=True)

    if has_toldos:
        toldo_loads = {"toldo_izq": 0.0, "toldo_der": 0.0}
        for item in returnable_items:
            placed = _pack_into_toldos_safely(
                item, toldo_loads, zones, toldo_cap,
            )
            if not placed:
                # No cupo en ningún toldo: lo enviamos a bahías sin desbordar.
                _pack_into_bays_safely(item, bay_ids, bay_loads, zones, bay_cap)
    else:
        for item in returnable_items:
            _pack_into_bays_safely(item, bay_ids, bay_loads, zones, bay_cap)

    ascii_plan = generate_truck_ascii_plan(zones, layout)
    safety_notes = generate_safety_notes(zones, layout)
    warehouse_preparation = generate_warehouse_picking_order(stops, zones, layout)

    return {
        'truck_code': layout["truck_code"],
        'truck_layout': {
            'n_rows': layout["n_rows"],
            'n_cols': layout["n_cols"],
            'n_bays': layout["n_bays"],
            'has_toldos': layout["has_toldos"],
            'bay_capacity_l': layout["bay_capacity_l"],
            'toldo_capacity_l': layout["toldo_capacity_l"],
            'row_codes': layout["row_codes"],
        },
        'ascii_plan': ascii_plan,
        'loading_zones': [
            {
                'zone_id': z.zone_id,
                'zone_name': z.zone_name,
                'access': z.access_type,
                'capacity_l': z.max_volume_l,
                'current_volume_l': sum(item['vol_l'] for item in z.items),
                'items': z.items,
            }
            for z in zones.values()
        ],
        'safety_notes': safety_notes,
        'warehouse_preparation': warehouse_preparation,
    }


def _bay_ids_from_layout(layout: dict) -> list[str]:
    return [
        f"bay_{r}{c}"
        for r in layout["row_codes"]
        for c in range(1, layout["n_cols"] + 1)
    ]


def generate_truck_ascii_plan(zones: dict, layout: dict) -> str:
    """Genera una vista ASCII del camión cargado, adaptada al layout."""
    lines = []
    truck_code = layout.get("truck_code", "?")
    n_rows = layout["n_rows"]
    n_cols = layout["n_cols"]
    has_toldos = layout["has_toldos"]
    bay_ids = _bay_ids_from_layout(layout)

    total_truck_cap = int(round(layout["bay_capacity_l"] * layout["n_bays"]))

    lines.append("\n" + "=" * 80)
    lines.append(f"CAMION {truck_code} - VISTA DESDE ARRIBA (PLANO TOP-DOWN)")
    lines.append("=" * 80)
    lines.append(f"({n_rows} fila{'s' if n_rows > 1 else ''} × {n_cols} columnas · "
                 f"{'con' if has_toldos else 'sin'} toldos laterales)")
    lines.append("")
    lines.append("  FRENTE DEL VEHICULO (Cabina)")

    if has_toldos:
        lines.append("  [TOLDO IZQ ←]                         [→ TOLDO DER]")

    sep = "  +" + "------+" * n_cols
    lines.append(sep)
    for rcode in layout["row_codes"]:
        cells = []
        for c in range(1, n_cols + 1):
            zid = f"bay_{rcode}{c}"
            zone = zones[zid]
            vol = sum(i['vol_l'] for i in zone.items)
            pct = int(100 * vol / zone.max_volume_l) if zone.max_volume_l else 0
            cells.append(f" {_ROW_LABEL[rcode]}{c}{pct:3d}%")
        lines.append("  |" + "|".join(cells) + " |")
        lines.append(sep)

    if has_toldos:
        vol_izq = sum(i['vol_l'] for i in zones['toldo_izq'].items)
        vol_der = sum(i['vol_l'] for i in zones['toldo_der'].items)
        cap_izq = zones['toldo_izq'].max_volume_l
        cap_der = zones['toldo_der'].max_volume_l
        pct_izq = int(100 * vol_izq / cap_izq) if cap_izq else 0
        pct_der = int(100 * vol_der / cap_der) if cap_der else 0
        lines.append(f"  TOLDO IZQ: {vol_izq:5.0f}/{cap_izq}L ({pct_izq:3d}%)   "
                     f"TOLDO DER: {vol_der:5.0f}/{cap_der}L ({pct_der:3d}%)")

    lines.append("              ||")
    lines.append("           RAMPA TRASERA (descarga principal)")
    lines.append("=" * 80)

    lines.append("\n[RESUMEN DE OCUPACION]")
    lines.append("-" * 80)
    total_vol = sum(i['vol_l'] for z in zones.values() for i in z.items)
    pct_total = (100 * total_vol / total_truck_cap) if total_truck_cap else 0
    lines.append(f"VOLUMEN TOTAL: {total_vol:7.1f} L / {total_truck_cap} L ({pct_total:5.1f}%)")

    lines.append("\nDISTRIBUCION POR BAHIA:")
    for zid in bay_ids:
        zone = zones[zid]
        vol = sum(i['vol_l'] for i in zone.items)
        kg = sum(i['peso_kg'] for i in zone.items)
        pct = 100 * vol / zone.max_volume_l if zone.max_volume_l else 0
        label = zid.replace("bay_", "").upper()
        lines.append(f"  {label}: {vol:7.1f}L ({pct:5.1f}%) | {kg:7.1f}kg")

    if has_toldos:
        lines.append("\nRETORNABLES (Toldos Laterales):")
        lines.append(f"  Toldo Izquierdo: {sum(i['vol_l'] for i in zones['toldo_izq'].items):7.1f}L "
                     f"/ {zones['toldo_izq'].max_volume_l}L")
        lines.append(f"  Toldo Derecho:   {sum(i['vol_l'] for i in zones['toldo_der'].items):7.1f}L "
                     f"/ {zones['toldo_der'].max_volume_l}L")
    else:
        lines.append("\nRETORNABLES: distribuidos en bahías (este camión no tiene toldos)")

    lines.append("\n" + "-" * 80)
    lines.append("[DETALLE POR BAHIA]")
    lines.append("-" * 80)
    for zid in bay_ids:
        zone = zones[zid]
        label = zid.replace("bay_", "").upper()
        lines.append(f"\n{label} - {zone.zone_name}")
        lines.append(f"  Capacidad: {zone.max_volume_l}L")
        lines.append(f"  Actual:    {sum(i['vol_l'] for i in zone.items):.0f}L")
        lines.append(f"  Acceso:    {zone.access_type}")
        for item in sorted(zone.items, key=lambda x: x['vol_l'], reverse=True):
            lines.append(f"    * {item['name']:30s} {item['vol_l']:7.1f}L {item['peso_kg']:7.1f}kg")

    if has_toldos:
        for tid, tlabel in [("toldo_izq", "TOLDO LATERAL IZQUIERDO"),
                            ("toldo_der", "TOLDO LATERAL DERECHO")]:
            lines.append(f"\n{tlabel} (Retornables)")
            lines.append(f"  Capacidad: {zones[tid].max_volume_l}L")
            for item in zones[tid].items:
                lines.append(f"    * {item['name']:30s} {item['vol_l']:7.1f}L")

    lines.append("\n" + "=" * 80)
    return "\n".join(lines)


def generate_safety_notes(zones: dict, layout: dict) -> list[str]:
    """Genera notas de seguridad para la carga, adaptadas al layout."""
    notes: list[str] = []
    bay_ids = _bay_ids_from_layout(layout)

    bay_loads = {bid: sum(i['vol_l'] for i in zones[bid].items) for bid in bay_ids}
    if bay_loads:
        max_load = max(bay_loads.values())
        min_load = min(bay_loads.values())
        imbalance = (max_load - min_load) / (max_load + 0.001) if max_load > 0 else 0
        if imbalance > 0.3:
            notes.append(f"! ALERTA: Distribucion desequilibrada entre bahías ({imbalance*100:.0f}%)")
        elif imbalance > 0.15:
            notes.append("⚠ Bahías con cargas desiguales - verifica estabilidad")
        else:
            notes.append("✓ Distribucion equilibrada entre bahías")

    for bid in bay_ids:
        zone = zones[bid]
        vol = sum(i['vol_l'] for i in zone.items)
        if vol > zone.max_volume_l * 1.1:
            notes.append(f"! ALERTA: BAHIA SOBRECARGADA ({zone.zone_name}: {vol:.0f}L > {zone.max_volume_l}L)")
        elif vol > zone.max_volume_l * 0.9:
            notes.append(f"⚠ {zone.zone_name} casi llena ({vol:.0f}L / {zone.max_volume_l}L)")

    if layout["has_toldos"]:
        toldo_izq_vol = sum(i['vol_l'] for i in zones['toldo_izq'].items)
        toldo_der_vol = sum(i['vol_l'] for i in zones['toldo_der'].items)
        toldo_cap = zones['toldo_izq'].max_volume_l
        if toldo_izq_vol > 0 or toldo_der_vol > 0:
            notes.append(f"✓ Retornables en toldos laterales ({toldo_izq_vol + toldo_der_vol:.0f}L "
                         f"de {2 * toldo_cap}L disponibles)")
            if toldo_izq_vol > 0:
                notes.append(f"  - Toldo Izquierdo: {toldo_izq_vol:.0f}L / {toldo_cap}L")
            if toldo_der_vol > 0:
                notes.append(f"  - Toldo Derecho:   {toldo_der_vol:.0f}L / {toldo_cap}L")
        else:
            notes.append("ℹ Sin retornables en esta ruta")
    else:
        notes.append("ℹ Camión sin toldos laterales: retornables ubicados en bahías")

    notes.append("")
    notes.append("INSTRUCCIONES DE CARGA:")
    notes.append("1. Usar acceso trasero (rampa principal) para bahías")
    if layout["n_rows"] > 1:
        notes.append("2. Distribuir peso equilibradamente entre bahías N-S")
    else:
        notes.append("2. Distribuir peso equilibradamente a lo largo de la cabina")
    if layout["has_toldos"]:
        notes.append("3. Retornables siempre en toldos laterales cuando quepan (acceso fácil)")
    else:
        notes.append("3. Retornables al fondo: acceso por rampa trasera")
    notes.append("4. Asegurar cargas con flejes metalicos")
    notes.append("5. Respetar altura maxima techo (2.1m)")
    notes.append("6. Revisar presion de neumaticos antes de salir")
    return notes


def generate_warehouse_picking_order(stops: list[dict], zones: dict, layout: dict) -> list[dict]:
    """Genera el orden de picking en almacén (LIFO - último cliente primero)."""
    picking_order = []
    
    for stop in reversed(stops):
        materiales_by_uma = defaultdict(list)
        
        for mat in stop.get('materiales', []):
            uma = mat.get('uma', 'UNIT')
            materiales_by_uma[uma].append(mat)
        
        uma_priority = {'BRL': 0, 'BID': 1, 'BOT': 2, 'CAJ': 3, 'EST': 3, 'UN': 4, 'TB': 4}
        
        picking_entry = {
            'order_position': len(stops) - stop['order'] + 1,
            'route_order': stop['order'],
            'cliente_nombre': stop['cliente_nombre'],
            'poblacion': stop['poblacion'],
            'picking_sequence': []
        }
        
        # Mapeo informativo UMA → tipo de zona, según el layout del camión
        bay_ids = _bay_ids_from_layout(layout)
        # Pesados (BRL/BID/BOT) → bahías delanteras (estabilidad). Resto → traseras.
        n_cols = layout["n_cols"]
        front_cols = max(1, n_cols // 2)
        front_bays = "/".join(bid for bid in bay_ids if int(bid[-1]) <= front_cols) or bay_ids[0]
        back_bays = "/".join(bid for bid in bay_ids if int(bid[-1]) > front_cols) or bay_ids[-1]
        uma_to_bay = {
            'BRL': front_bays, 'BID': front_bays, 'BOT': front_bays,
            'CAJ': back_bays, 'EST': back_bays, 'UN': back_bays, 'TB': back_bays,
        }
        
        for uma in sorted(materiales_by_uma.keys(), key=lambda x: uma_priority.get(x, 99)):
            items = materiales_by_uma[uma]
            picking_entry['picking_sequence'].append({
                'uma': uma,
                'bahia': uma_to_bay.get(uma, 'bahia_asignada'),
                'cantidad': len(items),
                'volumen_l': sum(it.get('vol_l', 0) for it in items),
                'peso_kg': sum(it.get('peso_kg', 0) for it in items)
            })
        
        picking_order.append(picking_entry)
    
    return picking_order


def format_loading_plan_for_terminal(plan: dict) -> str:
    """Formatea el plan de carga para impresión en terminal."""
    output = []
    
    output.append(plan['ascii_plan'])
    
    output.append("\n[NOTAS DE SEGURIDAD Y ESTABILIDAD]:")
    for note in plan['safety_notes']:
        output.append(f"  {note}")
    
    output.append("\n" + "="*70)
    output.append("[ORDEN DE PICKING EN ALMACEN] (LIFO - Ultimos clientes primero)")
    output.append("="*70)
    
    for i, picking in enumerate(plan['warehouse_preparation'], 1):
        output.append(f"\nPASO_ALMACEN [{i:2d}] Cliente {picking['cliente_nombre']:25s} -> Parada {picking['route_order']:2d}")
        output.append(f"           Poblacion: {picking['poblacion']}")
        
        for seq in picking['picking_sequence']:
            bahia = seq['bahia']
            uma = seq['uma']
            cantidad = seq['cantidad']
            volumen_l = seq['volumen_l']
            peso_kg = seq['peso_kg']
            output.append(f"\n           BAHIA {bahia:15s} [{uma}]: {cantidad} lineas | {volumen_l:.0f}L | {peso_kg:.0f}kg")
    
    return "\n".join(output)


def export_loading_plan_html(plan: dict[str, Any], output_path: str, title: str = "Plan de carga") -> str:
    """Exporta el plan de carga a HTML para demo/presentación."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    zones = plan.get("loading_zones", [])
    safety_notes = plan.get("safety_notes", [])
    warehouse_preparation = plan.get("warehouse_preparation", [])

    zone_cards = []
    for z in zones:
        current = float(z.get("current_volume_l", 0))
        capacity = float(z.get("capacity_l", 0)) or 1.0
        pct = 100 * current / capacity
        zone_cards.append(
            f"""
            <div class="zone-card">
                <div class="zone-head">
                    <div class="zone-name">{z.get('zone_name','')}</div>
                    <div class="zone-tag">{z.get('zone_id','')}</div>
                </div>
                <div class="bar"><div class="bar-fill" style="width:{min(pct, 100):.1f}%"></div></div>
                <div class="zone-meta">{current:.0f} / {capacity:.0f} L · {pct:.1f}%</div>
                <div class="zone-meta muted">{z.get('access','')}</div>
            </div>
            """
        )

    picking_cards = []
    for p in warehouse_preparation[:12]:
        seq_txt = []
        for seq in p.get("picking_sequence", []):
            seq_txt.append(f"{seq.get('uma','?')} → {seq.get('bahia','bahia')} ({seq.get('cantidad', 0)} uds)")
        picking_cards.append(
            f"""
            <div class="pick-card">
                <div class="pick-title">{p.get('cliente_nombre','')}</div>
                <div class="pick-sub">Parada {p.get('route_order','')} · {p.get('poblacion','')}</div>
                <div class="pick-list">{'<br>'.join(seq_txt)}</div>
            </div>
            """
        )

    notes_html = "".join(f"<li>{n}</li>" for n in safety_notes)

    html = f"""<!doctype html>
<html lang="es">
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <style>
        :root {{ --bg:#f8fafc; --card:#ffffff; --text:#0f172a; --muted:#64748b; --line:#e2e8f0; --accent:#2563eb; }}
        body {{ font-family: Inter, Arial, sans-serif; margin: 24px; background: var(--bg); color: var(--text); line-height: 1.4; }}
        h1 {{ margin: 0 0 6px; }}
        .subtitle {{ color: var(--muted); margin-bottom: 18px; }}
        .grid {{ display: grid; gap: 14px; }}
        .kpis {{ grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); display:grid; gap:12px; margin-bottom: 18px; }}
        .kpi {{ background: var(--card); border:1px solid var(--line); border-radius:16px; padding:14px; box-shadow: 0 8px 20px rgba(15, 23, 42, 0.05); }}
        .kpi .label {{ color: var(--muted); font-size: 12px; font-weight: 700; text-transform: uppercase; }}
        .kpi .value {{ font-size: 26px; font-weight: 800; margin-top: 4px; }}
        .card {{ background: var(--card); border:1px solid var(--line); border-radius: 18px; padding: 16px; box-shadow: 0 8px 20px rgba(15, 23, 42, 0.05); margin-bottom: 16px; }}
        .mono {{ white-space: pre-wrap; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; background:#0b1220; color:#e2e8f0; border-radius:14px; padding:14px; overflow:auto; }}
        .zone-grid, .pick-grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }}
        .zone-card, .pick-card {{ background: #fff; border:1px solid var(--line); border-radius: 16px; padding: 14px; }}
        .zone-head {{ display:flex; justify-content:space-between; gap:12px; align-items:flex-start; margin-bottom:8px; }}
        .zone-name {{ font-weight: 800; }}
        .zone-tag {{ background:#dbeafe; color:#1e3a8a; padding:4px 8px; border-radius:999px; font-size:12px; font-weight:700; }}
        .zone-meta {{ font-size: 13px; color: var(--text); margin-top: 6px; }}
        .muted {{ color: var(--muted); }}
        .bar {{ width: 100%; height: 10px; background: #e2e8f0; border-radius:999px; overflow:hidden; }}
        .bar-fill {{ height: 100%; background: linear-gradient(90deg, #38bdf8, #2563eb); }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ border: 1px solid var(--line); padding: 10px; text-align:left; vertical-align: top; }}
        th {{ background: #0f172a; color: white; }}
        ul {{ margin: 0; padding-left: 20px; }}
        .small {{ font-size: 13px; color: var(--muted); }}
        .pick-title {{ font-weight: 800; margin-bottom: 4px; }}
        .pick-sub {{ color: var(--muted); font-size: 13px; margin-bottom: 8px; }}
        .pick-list {{ font-size: 13px; line-height: 1.45; }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    <div class="subtitle">Visualización ejecutiva del plan de carga, secuencia de picking y estabilidad por bahías.</div>

    <div class="kpis">
        <div class="kpi"><div class="label">Zonas</div><div class="value">{len(zones)}</div><div class="small">4 bahías + 2 toldos</div></div>
        <div class="kpi"><div class="label">Paradas</div><div class="value">{len(warehouse_preparation)}</div><div class="small">Preparación en almacén</div></div>
        <div class="kpi"><div class="label">Notas</div><div class="value">{len(safety_notes)}</div><div class="small">Seguridad y equilibrio</div></div>
        <div class="kpi"><div class="label">Capacidad visual</div><div class="value">4-bay</div><div class="small">Top-down, N-S</div></div>
    </div>

    <div class="card">
        <h2>Vista ASCII</h2>
        <div class="mono">{plan.get('ascii_plan', '').replace('<', '&lt;').replace('>', '&gt;')}</div>
    </div>

    <div class="card">
        <h2>Zonas de carga</h2>
        <div class="zone-grid">{''.join(zone_cards)}</div>
    </div>

    <div class="card">
        <h2>Notas de seguridad</h2>
        <ul>{notes_html}</ul>
    </div>

    <div class="card">
        <h2>Orden de picking (almacén)</h2>
        <div class="pick-grid">{''.join(picking_cards)}</div>
    </div>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")
    return str(path)


class TruckVisualizer:
    """
    Clase encargada de generar representaciones visuales 2D de la carga del camión
    basadas en los objetos TruckLoad y Bay generados por el packer.
    """
    
    # Paleta de colores consistente para los tipos de productos
    COLORS = {
        "BARRIL": "#8B4513",   # Marrón oscuro
        "CAJA": "#1f77b4",     # Azul
        "PACK": "#00CED1",     # Cian
        "ESTUCHE": "#9467bd",  # Morado
        "UNIDAD": "#ff7f0e",   # Naranja
        "MIXTO": "#7f7f7f"     # Gris
    }

    @staticmethod
    def get_truck_dimensions(truck_type: str) -> tuple[int, int]:
        """
        Devuelve la configuración física del camión: (número_de_jaulas, palets_por_jaula)
        """
        if truck_type == "8P":
            return 4, 2  # 4 jaulas, 2 de profundidad (8 palets)
        elif truck_type == "6P":
            return 3, 2  # 3 jaulas, 2 de profundidad (6 palets)
        elif truck_type == "3P":
            return 3, 1  # 3 jaulas, 1 de profundidad (3 palets - furgoneta)
        return 3, 2  # Fallback por defecto (6P)

    @classmethod
    def plot_2d_views(cls, load) -> go.Figure:
        """
        Genera una figura de Plotly con las vistas superior y lateral de la carga.
        :param load: Instancia de TruckLoad (proveniente de src.packer)
        :return: Objeto go.Figure listo para Streamlit
        """
        n_jaulas, pallets_per_jaula = cls.get_truck_dimensions(load.truck_type)
        
        fig = make_subplots(
            rows=2, cols=1, 
            subplot_titles=(
                "⬇️ VISTA DESDE ARRIBA (Planta del Contenedor - Distribución de Palets)", 
                "➡️ VISTA LATERAL (Apilamiento de Lonas - Suelo a Techo)"
            ),
            vertical_spacing=0.15
        )

        # =========================================================
        # 1. VISTA DESDE ARRIBA (Shapes para simular el contenedor)
        # =========================================================
        for jaula_idx in range(n_jaulas):
            # Dibujar el contorno de la jaula
            fig.add_shape(
                type="rect",
                x0=jaula_idx, y0=0, x1=jaula_idx + 0.95, y1=pallets_per_jaula,
                line=dict(color="#333333", width=4),
                fillcolor="rgba(240, 240, 240, 0.4)",
                row=1, col=1
            )
            # Etiqueta indicativa de la Jaula
            fig.add_annotation(
                x=jaula_idx + 0.475, y=pallets_per_jaula + 0.15,
                text=f"Jaula {jaula_idx + 1}", showarrow=False,
                font=dict(size=14, weight="bold", color="#555"),
                row=1, col=1
            )

        # Ubicamos los clientes/palets en la vista desde arriba
        for bay in load.bays:
            if not bay.items: 
                continue
                
            # Calcular posición lógica de la bahía (palet) en la jaula
            jaula_idx = bay.index // pallets_per_jaula
            pos_y = bay.index % pallets_per_jaula
            
            # Formatear el texto a mostrar
            cliente_nombre = bay.items[0].cliente_nombre
            cliente_corto = cliente_nombre[:15] + "..." if len(cliente_nombre) > 15 else cliente_nombre
            
            fig.add_annotation(
                x=jaula_idx + 0.475, y=pos_y + 0.5,
                text=f"<b>{cliente_corto}</b><br>{bay.vol_usado_l:.0f} L<br>{bay.peso_kg:.0f} kg",
                showarrow=False,
                font=dict(color="#000000", size=11),
                bgcolor="rgba(255, 255, 255, 0.9)", 
                bordercolor="#333333", borderwidth=1, borderpad=4,
                row=1, col=1
            )

        # =========================================================
        # 2. VISTA LATERAL (Stacked Bar Chart para el apilamiento)
        # =========================================================
        for bay in load.bays:
            jaula_idx = bay.index // pallets_per_jaula
            
            # Iteramos sobre los items de la bahía (que ya deberían venir ordenados por estabilidad del packer)
            for item in bay.items:
                color = cls.COLORS.get(item.tipo_dominante, cls.COLORS["MIXTO"])
                
                fig.add_trace(go.Bar(
                    x=[f"Jaula {jaula_idx + 1}"],
                    y=[max(item.altura_estimada_cm, 15)],  # Altura mínima visual para que siempre se vea algo
                    name=item.tipo_dominante,
                    marker_color=color,
                    marker_line=dict(color='black', width=1),
                    text=f"{item.tipo_dominante}<br>{item.cliente_nombre[:12]}",
                    textposition='inside',
                    insidetextanchor='middle',
                    hovertext=f"<b>Cliente:</b> {item.cliente_nombre}<br><b>Peso:</b> {item.peso_kg:.1f}kg<br><b>Vol:</b> {item.volumen_l:.1f}L",
                    hoverinfo="text",
                    showlegend=False
                ), row=2, col=1)

        # Añadimos la leyenda de colores una sola vez (hacks de Plotly)
        for prod_type, color in cls.COLORS.items():
            fig.add_trace(go.Bar(x=[None], y=[None], name=prod_type, marker_color=color), row=2, col=1)

        # =========================================================
        # CONFIGURACIÓN DEL LAYOUT
        # =========================================================
        fig.update_layout(
            barmode='stack',  # Apila las barras simulando gravedad
            height=800,       # Altura total del componente
            plot_bgcolor="rgba(255, 255, 255, 1)",
            paper_bgcolor="rgba(255, 255, 255, 1)",
            margin=dict(t=60, b=50, l=40, r=40),
            legend=dict(orientation="h", yanchor="bottom", y=-0.1, xanchor="center", x=0.5)
        )
        
        # Ocultar ejes en la vista superior (planta)
        fig.update_xaxes(showgrid=False, visible=False, row=1, col=1)
        fig.update_yaxes(showgrid=False, visible=False, range=[-0.2, pallets_per_jaula + 0.4], row=1, col=1)
        
        # Limpiar ejes en la vista lateral
        fig.update_xaxes(title_text="", showgrid=False, row=2, col=1)
        fig.update_yaxes(title_text="Altura del Apilamiento (cm)", showgrid=True, gridcolor="#e0e0e0", row=2, col=1)

        return fig