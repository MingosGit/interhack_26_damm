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


def visualize_loading_plan(stops: list[dict], truck_capacity_l: int) -> dict[str, Any]:
    """
    Crea un plan de carga visualizado para el camión REAL (4 bahías longitudinales).
    """
    # ===== DEFINIR BAHIAS DEL CAMIÓN (estructura real) =====
    zones = {
        'bay_no1': LoadingZone(
            zone_id='bay_no1', zone_name='BAHIA NORTE-1 (frente, lado norte)',
            width=20, height=4, max_volume_l=3600, access_type='trasero/lateral_norte',
        ),
        'bay_no2': LoadingZone(
            zone_id='bay_no2', zone_name='BAHIA NORTE-2 (atrás, lado norte)',
            width=20, height=4, max_volume_l=3600, access_type='trasero/lateral_norte',
        ),
        'bay_su1': LoadingZone(
            zone_id='bay_su1', zone_name='BAHIA SUR-1 (frente, lado sur)',
            width=20, height=4, max_volume_l=3600, access_type='trasero/lateral_sur',
        ),
        'bay_su2': LoadingZone(
            zone_id='bay_su2', zone_name='BAHIA SUR-2 (atrás, lado sur)',
            width=20, height=4, max_volume_l=3600, access_type='trasero/lateral_sur',
        ),
        'toldo_izq': LoadingZone(
            zone_id='toldo_izq', zone_name='TOLDO LATERAL IZQUIERDO (retornables)',
            width=8, height=3, max_volume_l=1500, access_type='lateral_deslizable',
        ),
        'toldo_der': LoadingZone(
            zone_id='toldo_der', zone_name='TOLDO LATERAL DERECHO (retornables)',
            width=8, height=3, max_volume_l=1500, access_type='lateral_deslizable',
        ),
    }
    
    # ===== CLASIFICAR ITEMS Y DISTRIBUIR EQUILIBRADAMENTE =====
    all_items_by_uma = defaultdict(lambda: {'cantidad': 0, 'vol_l': 0, 'peso_kg': 0, 'stops': []})
    
    for stop in stops:
        for mat in stop.get('materiales', []):
            uma = mat.get('uma', 'UNIT')
            mat_key = f"{mat.get('material', '?')} ({uma})"
            all_items_by_uma[mat_key]['cantidad'] += mat.get('cantidad', 0)
            all_items_by_uma[mat_key]['vol_l'] += mat.get('vol_l', 0)
            all_items_by_uma[mat_key]['peso_kg'] += mat.get('peso_kg', 0)
            all_items_by_uma[mat_key]['stops'].append(stop['order'])
            all_items_by_uma[mat_key]['retornable'] = mat.get('retornable', False)
    
    heavy_items = []  
    light_items = []  
    returnable_items = []
    
    uma_priority = {'BRL': 0, 'BID': 1, 'BOT': 2, 'CAJ': 3, 'UN': 4, 'TB': 4, 'EST': 3}
    
    for item_key, item_data in sorted(all_items_by_uma.items(), 
                                       key=lambda x: uma_priority.get(x[0].split('(')[-1].rstrip(')'), 99)):
        uma = item_key.split('(')[-1].rstrip(')')
        item_obj = {
            'name': item_key,
            'cantidad': item_data['cantidad'],
            'vol_l': item_data['vol_l'],
            'peso_kg': item_data['peso_kg'],
            'stops': item_data['stops'],
            'retornable': item_data.get('retornable', False),
        }
        
        if item_data.get('retornable', False):
            returnable_items.append(item_obj)
        elif uma in ('BRL', 'BID', 'BOT'):
            heavy_items.append(item_obj)
        else:
            light_items.append(item_obj)
    
    bay_order = ['bay_no1', 'bay_no2', 'bay_su1', 'bay_su2']
    bay_loads = {bay: 0.0 for bay in bay_order}
    
    all_sorted = sorted(heavy_items + light_items, key=lambda x: x['peso_kg'], reverse=True)
    for item in all_sorted:
        min_bay = min(bay_loads, key=bay_loads.get)
        zones[min_bay].items.append(item)
        bay_loads[min_bay] += item['vol_l']
    
    for i, item in enumerate(returnable_items):
        toldo = 'toldo_izq' if i % 2 == 0 else 'toldo_der'
        zones[toldo].items.append(item)
    
    ascii_plan = generate_truck_ascii_plan(zones)
    safety_notes = generate_safety_notes(zones)
    warehouse_preparation = generate_warehouse_picking_order(stops, zones)
    
    return {
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


def generate_truck_ascii_plan(zones: dict) -> str:
    """Genera una vista ASCII del camión cargado."""
    lines = []
    
    lines.append("\n" + "="*80)
    lines.append("CAMION 6P - VISTA DESDE ARRIBA (PLANO TOP-DOWN)")
    lines.append("="*80)
    lines.append("(Eje Y = dirección de marcha frente->atrás)")
    lines.append("(Eje X = ancho del camión lado norte->sur)")
    lines.append("")
    
    bay_data = {
        'bay_no1': (zones['bay_no1'], 'NO1'),
        'bay_no2': (zones['bay_no2'], 'NO2'),
        'bay_su1': (zones['bay_su1'], 'SU1'),
        'bay_su2': (zones['bay_su2'], 'SU2'),
    }
    
    lines.append("  FRENTE DEL VEHICULO (Cabina)")
    lines.append("  +---+---+---+---+---+---+---+---+---+---+")
    lines.append("  | TOLDO                  TOLDO LATERAL |")
    lines.append("  | LATERAL                 DERECHO      |")
    lines.append("  | IZQ                                   |")
    lines.append("  +---+---+---+---+---+---+---+---+---+---+")
    
    for row_num in range(1, 3):
        bay_n = f'bay_no{row_num}'
        bay_s = f'bay_su{row_num}'
        zone_n, label_n = bay_data[bay_n]
        zone_s, label_s = bay_data[bay_s]
        
        vol_n = sum(i['vol_l'] for i in zone_n.items)
        vol_s = sum(i['vol_l'] for i in zone_s.items)
        pct_n = int(100 * vol_n / zone_n.max_volume_l) if zone_n.max_volume_l else 0
        pct_s = int(100 * vol_s / zone_s.max_volume_l) if zone_s.max_volume_l else 0
        
        bar_n = '#' * (pct_n // 5) + '.' * (20 - pct_n // 5)
        bar_s = '#' * (pct_s // 5) + '.' * (20 - pct_s // 5)
        
        lines.append(f"  | {bar_n} | {bar_s} |  ")
        lines.append(f"  | {label_n}: {vol_n:5.0f}L {pct_n:3d}%     | {label_s}: {vol_s:5.0f}L {pct_s:3d}%     |")
        
        top_n = sorted(zone_n.items, key=lambda x: x['vol_l'], reverse=True)[:1]
        top_s = sorted(zone_s.items, key=lambda x: x['vol_l'], reverse=True)[:1]
        prod_n = top_n[0]['name'][:20] if top_n else "VACIO"
        prod_s = top_s[0]['name'][:20] if top_s else "VACIO"
        lines.append(f"  | {prod_n[:20]:<20} | {prod_s[:20]:<20} |")
        
    vol_izq = sum(i['vol_l'] for i in zones['toldo_izq'].items)
    vol_der = sum(i['vol_l'] for i in zones['toldo_der'].items)
    pct_izq = int(100 * vol_izq / zones['toldo_izq'].max_volume_l) if zones['toldo_izq'].max_volume_l else 0
    pct_der = int(100 * vol_der / zones['toldo_der'].max_volume_l) if zones['toldo_der'].max_volume_l else 0
    
    lines.append("  +---+---+---+---+---+---+---+---+---+---+")
    lines.append(f"  | TOLDO IZQ: {vol_izq:5.0f}L {pct_izq:3d}% | TOLDO DER: {vol_der:5.0f}L {pct_der:3d}% |")
    lines.append("  | (Retornables - Lado Izq/Derecho)      |")
    lines.append("  +---+---+---+---+---+---+---+---+---+---+")
    lines.append("              ||")
    lines.append("           RAMPA TRASERA (descarga principal)")
    lines.append("="*80)
    
    lines.append("\n[RESUMEN DE OCUPACION]")
    lines.append("-"*80)
    total_vol = sum(i['vol_l'] for i in sum([z.items for z in zones.values()], []))
    lines.append(f"VOLUMEN TOTAL: {total_vol:7.1f} L / {14400} L ({100*total_vol/14400:5.1f}%)")
    
    lines.append("\nDISTRIBUCION POR BAHIA:")
    for bay_id, (zone, label) in bay_data.items():
        vol = sum(i['vol_l'] for i in zone.items)
        kg = sum(i['peso_kg'] for i in zone.items)
        pct = 100 * vol / zone.max_volume_l if zone.max_volume_l else 0
        lines.append(f"  {label}: {vol:7.1f}L ({pct:5.1f}%) | {kg:7.1f}kg")
    
    lines.append("\nRETORNABLES (Toldos Laterales):")
    lines.append(f"  Toldo Izquierdo: {vol_izq:7.1f}L ({pct_izq:5.1f}%)")
    lines.append(f"  Toldo Derecho:   {vol_der:7.1f}L ({pct_der:5.1f}%)")
    
    lines.append("\n" + "-"*80)
    lines.append("[DETALLE POR BAHIA]")
    lines.append("-"*80)
    
    for bay_id, (zone, label) in bay_data.items():
        lines.append(f"\n{label} - {zone.zone_name}")
        lines.append(f"  Capacidad: {zone.max_volume_l}L")
        lines.append(f"  Actual: {sum(i['vol_l'] for i in zone.items):.0f}L")
        lines.append(f"  Acceso: {zone.access_type}")
        for item in sorted(zone.items, key=lambda x: x['vol_l'], reverse=True):
            lines.append(f"    * {item['name']:30s} {item['vol_l']:7.1f}L {item['peso_kg']:7.1f}kg")
    
    lines.append(f"\nTOLDO LATERAL IZQUIERDO (Retornables)")
    lines.append(f"  Capacidad: {zones['toldo_izq'].max_volume_l}L")
    for item in zones['toldo_izq'].items:
        lines.append(f"    * {item['name']:30s} {item['vol_l']:7.1f}L")
    
    lines.append(f"\nTOLDO LATERAL DERECHO (Retornables)")
    lines.append(f"  Capacidad: {zones['toldo_der'].max_volume_l}L")
    for item in zones['toldo_der'].items:
        lines.append(f"    * {item['name']:30s} {item['vol_l']:7.1f}L")
    
    lines.append("\n" + "="*80)
    
    return "\n".join(lines)


def generate_safety_notes(zones: dict) -> list[str]:
    """Genera notas de seguridad para la carga."""
    notes = []
    
    bay_loads = {
        'bay_no1': sum(i['vol_l'] for i in zones['bay_no1'].items),
        'bay_no2': sum(i['vol_l'] for i in zones['bay_no2'].items),
        'bay_su1': sum(i['vol_l'] for i in zones['bay_su1'].items),
        'bay_su2': sum(i['vol_l'] for i in zones['bay_su2'].items),
    }
    
    max_load = max(bay_loads.values()) if bay_loads.values() else 0
    min_load = min(bay_loads.values()) if bay_loads.values() else 0
    imbalance = (max_load - min_load) / (max_load + 0.001) if max_load > 0 else 0
    
    if imbalance > 0.3:
        notes.append(f"! ALERTA: Distribucion desequilibrada entre bahías ({imbalance*100:.0f}%)")
    elif imbalance > 0.15:
        notes.append(f"⚠ Bahías con cargas desiguales - verifica estabilidad")
    else:
        notes.append("✓ Distribucion equilibrada entre bahías")
    
    for bay_id in ['bay_no1', 'bay_no2', 'bay_su1', 'bay_su2']:
        zone = zones[bay_id]
        vol = sum(i['vol_l'] for i in zone.items)
        if vol > zone.max_volume_l * 1.1:
            notes.append(f"! ALERTA: BAHIA SOBRECARGADA ({zone.zone_name}: {vol:.0f}L > {zone.max_volume_l}L)")
        elif vol > zone.max_volume_l * 0.9:
            notes.append(f"⚠ {zone.zone_name} casi llena ({vol:.0f}L / {zone.max_volume_l}L)")
    
    toldo_izq_vol = sum(i['vol_l'] for i in zones['toldo_izq'].items)
    toldo_der_vol = sum(i['vol_l'] for i in zones['toldo_der'].items)
    
    if toldo_izq_vol > 0 or toldo_der_vol > 0:
        notes.append(f"✓ Retornables separados en toldos laterales ({toldo_izq_vol+toldo_der_vol:.0f}L)")
        if toldo_izq_vol > 0:
            notes.append(f"  - Toldo Izquierdo: {toldo_izq_vol:.0f}L (deslizable)")
        if toldo_der_vol > 0:
            notes.append(f"  - Toldo Derecho: {toldo_der_vol:.0f}L (deslizable)")
    else:
        notes.append("ℹ Sin retornables en esta ruta")
    
    notes.append("")
    notes.append("INSTRUCCIONES DE CARGA:")
    notes.append("1. Usar acceso trasero (rampa principal) para bahías")
    notes.append("2. Distribuir peso equilibradamente entre bahías N-S")
    notes.append("3. Retornables siempre en toldos laterales (acceso fácil)")
    notes.append("4. Asegurar cargas con flejes metalicos")
    notes.append("5. Respetar altura maxima techo (2.1m)")
    notes.append("6. Revisar presion de neumaticos antes de salir")
    
    return notes


def generate_warehouse_picking_order(stops: list[dict], zones: dict) -> list[dict]:
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
        
        uma_to_bay = {
            'BRL': 'bay_no1/bay_su1',  
            'BID': 'bay_no1/bay_su1',  
            'BOT': 'bay_no1/bay_su1',  
            'CAJ': 'bay_no2/bay_su2',  
            'EST': 'bay_no2/bay_su2',  
            'UN': 'bay_no2/bay_su2',   
            'TB': 'bay_no2/bay_su2',   
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