"""
Módulo de Visualización de Carga
Crea representaciones visuales (ASCII y JSON) de cómo quedará cargado el camión.

Visualiza:
- Planta del camión con distribución de palets
- Secuencia de carga desde almacén
- Zonas de acceso (lateral, trasero)
- Apilamiento seguro (barriles abajo, cajas arriba)
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any


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
    Crea un plan de carga visualizado para el camión.
    
    Estrategia:
    - Fondo (pesado): Barriles, bidones
    - Centro: Cajas de bebida (medianas)
    - Superior: Cajas pequeñas, unitarios
    - Lateral: Retornables a recoger
    
    Returns:
        {
            'ascii_plan': str - visualización ASCII
            'loading_zones': list - distribución por zonas
            'safety_notes': list - notas de seguridad
            'warehouse_preparation': list - orden de picking en almacén
        }
    """
    
    # ===== DEFINIR ZONAS DEL CAMIÓN =====
    zones = {
        'bottom': LoadingZone(
            zone_id='bottom',
            zone_name='FONDO (Barriles, Bidones - PESADO)',
            width=40,
            height=3,
            max_volume_l=3000,
            access_type='trasero',
        ),
        'middle': LoadingZone(
            zone_id='middle',
            zone_name='CENTRO (Cajas medianas)',
            width=40,
            height=4,
            max_volume_l=6000,
            access_type='trasero/lateral',
        ),
        'top': LoadingZone(
            zone_id='top',
            zone_name='SUPERIOR (Cajas pequeñas, ligeras)',
            width=40,
            height=3,
            max_volume_l=3000,
            access_type='superior',
        ),
        'side': LoadingZone(
            zone_id='side',
            zone_name='LATERAL (Retornables a recoger)',
            width=15,
            height=5,
            max_volume_l=2000,
            access_type='lateral',
        ),
    }
    
    # ===== CLASIFICAR ITEMS POR ZONA =====
    # Agregamos todos los items de todos los stops
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
    
    # Asignar items a zonas basado en tipo de UMA
    uma_to_zone = {
        'BRL': 'bottom',  # Barril = fondo (pesado)
        'BID': 'bottom',  # Bidón = fondo
        'BOT': 'bottom',  # Botella pesada = fondo
        'CAJ': 'middle',  # Caja = centro
        'UN': 'top',      # Unitario = arriba (ligero)
        'TB': 'top',      # Tubería/tubería = arriba
        'EST': 'middle',  # Estrellas (cajas) = centro
    }
    
    for item_key, item_data in all_items_by_uma.items():
        uma = item_key.split('(')[-1].rstrip(')')
        zone_id = uma_to_zone.get(uma, 'middle')
        
        zones[zone_id].items.append({
            'name': item_key,
            'cantidad': item_data['cantidad'],
            'vol_l': item_data['vol_l'],
            'peso_kg': item_data['peso_kg'],
            'stops': item_data['stops'],
            'retornable': item_data.get('retornable', False),
        })
    
    # ===== GENERAR VISUALIZACIÓN ASCII =====
    ascii_plan = generate_truck_ascii_plan(zones)
    
    # ===== GENERAR NOTAS DE SEGURIDAD =====
    safety_notes = generate_safety_notes(zones)
    
    # ===== GENERAR PLAN DE PICKING EN ALMACÉN =====
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
    
    lines.append("\n" + "="*70)
    lines.append("VISTA LATERAL DEL CAMION (CROSS-SECTION)")
    lines.append("="*70)
    
    # Vista lateral: altura y profundidad
    lines.append("\nVISTA DE ARRIBA (PLANO):")
    lines.append("    DEPOSITO [========== CAMION 6P ==========]")
    lines.append("    ENTRADA  |                              |  SALIDA")
    lines.append("             |  LATERAL (RETORNABLES)  |  |")
    lines.append("             |  [=========]            |  |")
    lines.append("             |  SUPERIOR (Ligeras)  [==|==]")
    lines.append("             |  CENTRO (Cajas)       [==|==]")
    lines.append("             |  FONDO (Pesado)       [==|==]")
    lines.append("             |                          | |")
    lines.append("             |============================| | <- Toldo lateral")
    
    lines.append("\n" + "-"*70)
    lines.append("DETALLE POR ZONA:")
    lines.append("-"*70)
    
    # Fondo
    lines.append(f"\n[ZONA FONDO] Barriles, Bidones (PESADO)")
    lines.append(f"  Acceso: Trasero (descarga directa)")
    lines.append(f"  Carga: {sum(i['vol_l'] for i in zones['bottom'].items):.0f}L "
                f"/ {zones['bottom'].max_volume_l}L")
    for item in sorted(zones['bottom'].items, key=lambda x: x['peso_kg'], reverse=True):
        stops_str = f"(paradas {item['stops'][:3]}...)" if len(item['stops']) > 3 else f"(paradas {item['stops']})"
        lines.append(f"    * {item['name']:20s} {item['cantidad']:3d}x {item['vol_l']:6.0f}L {stops_str}")
    
    # Centro
    lines.append(f"\n[ZONA CENTRO] Cajas medianas (BALANCEADO)")
    lines.append(f"  Acceso: Trasero/Lateral")
    lines.append(f"  Carga: {sum(i['vol_l'] for i in zones['middle'].items):.0f}L "
                f"/ {zones['middle'].max_volume_l}L")
    for item in sorted(zones['middle'].items, key=lambda x: x['vol_l'], reverse=True):
        stops_str = f"(paradas {item['stops'][:3]}...)" if len(item['stops']) > 3 else f"(paradas {item['stops']})"
        lines.append(f"    * {item['name']:20s} {item['cantidad']:3d}x {item['vol_l']:6.0f}L {stops_str}")
    
    # Superior
    lines.append(f"\n[ZONA SUPERIOR] Cajas pequeñas, Unitarios (LIGERO)")
    lines.append(f"  Acceso: Superior (apilado)")
    lines.append(f"  Carga: {sum(i['vol_l'] for i in zones['top'].items):.0f}L "
                f"/ {zones['top'].max_volume_l}L")
    for item in sorted(zones['top'].items, key=lambda x: x['vol_l'], reverse=True):
        stops_str = f"(paradas {item['stops'][:3]}...)" if len(item['stops']) > 3 else f"(paradas {item['stops']})"
        lines.append(f"    * {item['name']:20s} {item['cantidad']:3d}x {item['vol_l']:6.0f}L {stops_str}")
    
    # Lateral
    if zones['side'].items:
        lines.append(f"\n[ZONA LATERAL] Retornables a RECOGER (LOGISTICA INVERSA)")
        lines.append(f"  Acceso: Lateral (toldo deslizable)")
        lines.append(f"  Carga: {sum(i['vol_l'] for i in zones['side'].items):.0f}L "
                    f"/ {zones['side'].max_volume_l}L")
        for item in zones['side'].items:
            lines.append(f"    * {item['name']:20s} {item['cantidad']:3d}x {item['vol_l']:6.0f}L (retornable)")
    else:
        lines.append(f"\n[ZONA LATERAL] VACIA (sin retornables en esta ruta)")
    
    lines.append("\n" + "="*70)
    
    return "\n".join(lines)


def generate_safety_notes(zones: dict) -> list[str]:
    """Genera notas de seguridad para la carga."""
    notes = []
    
    # Verificar estabilidad
    if zones['bottom'].items and not zones['middle'].items:
        notes.append("! ALERTA: Bariles en fondo sin carga encima - riesgo de movimiento")
    
    if zones['top'].items and not zones['middle'].items:
        notes.append("! ALERTA: Cajas ligeras arriba sin fondo - inestable")
    
    # Verificar capacidades
    for zone_id, zone in zones.items():
        vol = sum(i['vol_l'] for i in zone.items)
        if vol > zone.max_volume_l * 1.1:
            notes.append(f"! ALERTA: Zona {zone.zone_name} SOBRECARGADA ({vol:.0f}L > {zone.max_volume_l}L)")
        elif vol > zone.max_volume_l * 0.9:
            notes.append(f"! Zona {zone.zone_name} casi llena ({vol:.0f}L / {zone.max_volume_l}L)")
    
    # Retornables
    if zones['side'].items:
        notes.append("OK Retornables separados en lateral - facilita descarga")
    
    # Recomendaciones
    notes.append("OK Cargar en orden: FONDO -> CENTRO -> SUPERIOR -> LATERAL")
    notes.append("OK Barriles asegurados al fondo con fleje metalico")
    notes.append("OK Cajas apiladas maximo 1.5m (respeta altura techo camion)")
    notes.append("OK Caja de herramientas y extintor accesibles en cabina")
    
    return notes


def generate_warehouse_picking_order(stops: list[dict], zones: dict) -> list[dict]:
    """Genera el orden de picking en almacén (LIFO - último cliente primero)."""
    picking_order = []
    
    # Orden inverso: último cliente de la ruta primero en almacén
    for stop in reversed(stops):
        materiales_by_uma = defaultdict(list)
        
        for mat in stop.get('materiales', []):
            uma = mat.get('uma', 'UNIT')
            materiales_by_uma[uma].append(mat)
        
        # Prioridad de picking: FONDO primero, luego CENTRO, SUPERIOR, LATERAL
        uma_priority = {'BRL': 0, 'BID': 1, 'BOT': 2, 'CAJ': 3, 'UN': 4, 'TB': 4}
        
        picking_entry = {
            'order_position': len(stops) - stop['order'] + 1,
            'route_order': stop['order'],
            'cliente_nombre': stop['cliente_nombre'],
            'poblacion': stop['poblacion'],
            'picking_sequence': []
        }
        
        for uma in sorted(materiales_by_uma.keys(), key=lambda x: uma_priority.get(x, 99)):
            items = materiales_by_uma[uma]
            picking_entry['picking_sequence'].append({
                'uma': uma,
                'zona': {
                    'BRL': 'FONDO', 'BID': 'FONDO', 'BOT': 'FONDO',
                    'CAJ': 'CENTRO', 'EST': 'CENTRO',
                    'UN': 'SUPERIOR', 'TB': 'SUPERIOR'
                }.get(uma, 'CENTRO'),
                'items': [
                    {
                        'material': m.get('material', ''),
                        'cantidad': m.get('cantidad', 0),
                        'denominacion': m.get('denominacion', '')[:40],
                    }
                    for m in items
                ]
            })
        
        picking_order.append(picking_entry)
    
    return picking_order


def format_loading_plan_for_terminal(plan: dict) -> str:
    """Formatea el plan de carga para impresión en terminal."""
    output = []
    
    output.append(plan['ascii_plan'])
    
    # Notas de seguridad
    output.append("\n[NOTAS DE SEGURIDAD Y ESTABILIDAD]:")
    for note in plan['safety_notes']:
        output.append(f"  {note}")
    
    # Plan de picking en almacén
    output.append("\n" + "="*70)
    output.append("[ORDEN DE PICKING EN ALMACEN] (LIFO - Ultimos clientes primero)")
    output.append("="*70)
    
    for i, picking in enumerate(plan['warehouse_preparation'], 1):
        output.append(f"\nPASO_ALMACEN [{i:2d}] Cliente {picking['cliente_nombre']:25s} -> Parada {picking['route_order']:2d}")
        output.append(f"           Poblacion: {picking['poblacion']}")
        
        for seq in picking['picking_sequence']:
            output.append(f"\n           ZONA {seq['zona']:10s} ({seq['uma']}): {len(seq['items'])} lineas")
            for item in seq['items']:
                output.append(f"             + {item['material']:8s} {item['cantidad']:3d}x -> {item['denominacion']}")
    
    return "\n".join(output)
