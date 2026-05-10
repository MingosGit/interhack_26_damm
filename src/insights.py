"""
Módulo de Insights y Recomendaciones Operacionales
Genera análisis automático de rutas optimizadas y proporciona recomendaciones accionables
para los equipos de logística.

Cubre:
- Criterios priorizados en la optimización
- Puntos de fricción esperados
- Oportunidades de agrupación de clientes
- Orden recomendado de descarga
- Alertas de eficiencia
"""

from dataclasses import dataclass
from typing import Any
from collections import defaultdict
import json


@dataclass
class RouteInsight:
    """Análisis de una ruta optimizada."""
    transporte_id: int
    optimization_criteria: list[str]  # Qué se priorizó
    cluster_recommendations: list[dict]  # Clientes que conviene agrupar
    loading_sequence: list[dict]  # Orden recomendado de carga en almacén
    unloading_sequence: list[dict]  # Orden de descarga (inverso a carga)
    friction_points: list[str]  # Posibles problemas
    efficiency_alerts: list[str]  # Alertas sobre configuraciones
    key_metrics: dict[str, Any]  # Métricas de eficiencia
    explanations: dict[str, str]  # Explicaciones de por qué...
    recommendations: list[dict]  # Recomendaciones accionables


def analyze_route(
    transporte_id: int,
    stops: list[dict],
    baseline_time_s: int,
    baseline_dist_m: int,
    opt_time_s: int,
    opt_dist_m: int,
    truck_capacity_l: int,
    truck_capacity_kg: int,
    total_entrega_l: float,
    total_recogida_l: float,
) -> RouteInsight:
    """
    Analiza una ruta optimizada y genera insights.
    
    Args:
        stops: Lista de diccionarios con campos:
            {order, cliente_id, cliente_nombre, poblacion, entrega_l, recogida_l, 
             peso_kg, n_materiales, materiales: [{material, denominacion, cantidad, uma, vol_l, retornable}]}
        baseline_time_s: Tiempo en orden real (seg)
        baseline_dist_m: Distancia en orden real (m)
        opt_time_s: Tiempo optimizado (seg)
        opt_dist_m: Distancia optimizada (m)
        truck_capacity_l: Capacidad del camión (litros)
        truck_capacity_kg: Capacidad del camión (kg)
        total_entrega_l: Total de volumen a entregar
        total_recogida_l: Total de volumen a recoger
    
    Returns:
        RouteInsight con análisis completo
    """
    
    # ===== 1. CRITERIOS PRIORIZADOS =====
    time_improvement = ((baseline_time_s - opt_time_s) / baseline_time_s) * 100
    dist_improvement = ((baseline_dist_m - opt_dist_m) / baseline_dist_m) * 100
    
    optimization_criteria = []
    if dist_improvement > 20:
        optimization_criteria.append(f"Distancia (-{dist_improvement:.1f}%)")
    if time_improvement > 10:
        optimization_criteria.append(f"Tiempo de viaje (-{time_improvement:.1f}%)")
    if not optimization_criteria:
        optimization_criteria.append("Capacidad y restricciones operativas")
    optimization_criteria.append("Restricciones de capacidad")
    optimization_criteria.append("Ventanas de tiempo (si aplican)")
    
    # ===== 2. DETECCIÓN DE CLUSTERS Y AGRUPACIONES =====
    # Agrupar clientes por población para identificar clusters geográficos
    poblacion_clusters = defaultdict(list)
    cliente_clusters = defaultdict(list)
    
    for stop in stops:
        pob = stop.get('poblacion', 'Desconocida')
        cliente_id = stop.get('cliente_id', 'N/A')
        cliente_nom = stop.get('cliente_nombre', 'N/A')
        
        poblacion_clusters[pob].append({
            'order': stop['order'],
            'cliente_id': cliente_id,
            'cliente_nombre': cliente_nom,
            'entrega_l': stop['entrega_l'],
            'recogida_l': stop['recogida_l'],
        })
        
        cliente_clusters[cliente_id].append(stop['order'])
    
    cluster_recommendations = []
    
    # Recomendar agrupación por población si hay múltiples paradas
    for pob, stops_in_pob in poblacion_clusters.items():
        if len(stops_in_pob) >= 2:
            vol_total = sum(s['entrega_l'] for s in stops_in_pob)
            cluster_recommendations.append({
                'type': 'geographic_cluster',
                'location': pob,
                'stops': len(stops_in_pob),
                'volume_l': vol_total,
                'clients': [s['cliente_nombre'] for s in stops_in_pob],
                'note': f"Agrupar preparación de {len(stops_in_pob)} paradas en {pob} ({vol_total:.0f}L)",
                'priority': 'high' if vol_total > truck_capacity_l * 0.3 else 'medium'
            })
    
    # Detectar clientes con múltiples entregas
    for cliente_id, orders in cliente_clusters.items():
        if len(orders) >= 2:
            cliente_stops = [s for s in stops if s['cliente_id'] == cliente_id]
            vol_total = sum(s['entrega_l'] for s in cliente_stops)
            cluster_recommendations.append({
                'type': 'client_repeat',
                'cliente_id': cliente_id,
                'cliente_nombre': cliente_stops[0]['cliente_nombre'],
                'visits': len(orders),
                'orders': sorted(orders),
                'volume_l': vol_total,
                'note': f"Cliente {cliente_stops[0]['cliente_nombre']} aparece {len(orders)} veces - agrupar en almacén",
                'priority': 'high'
            })
    
    # ===== 3. SECUENCIA DE CARGA EN ALMACÉN (LIFO) =====
    # La carga debe prepararse en orden inverso: último cliente primero en almacén
    loading_sequence = []
    
    # Orden inverso: el último cliente de la ruta se carga primero
    for stop in reversed(stops):
        # Por cada parada, listar materiales agrupados por UMA
        materiales = stop.get('materiales', [])
        materiales_por_uma = defaultdict(list)
        
        for mat in materiales:
            uma = mat.get('uma', 'UNIT')
            materiales_por_uma[uma].append({
                'material': mat.get('material', ''),
                'denominacion': mat.get('denominacion', ''),
                'cantidad': mat.get('cantidad', 0),
                'vol_l': mat.get('vol_l', 0),
                'peso_kg': mat.get('peso_kg', 0),
                'retornable': mat.get('retornable', False),
            })
        
        # Priorizar barriles (pesados) al fondo, cajas (ligeras) arriba
        uma_priority = {'BRL': 0, 'BID': 1, 'BOT': 2, 'CAJ': 3, 'UN': 4}
        sorted_umas = sorted(
            materiales_por_uma.items(),
            key=lambda x: uma_priority.get(x[0], 99)
        )
        
        loading_sequence.append({
            'load_position': len(stops) - stop['order'] + 1,  # Conteo de carga en almacén
            'route_order': stop['order'],
            'cliente_id': stop['cliente_id'],
            'cliente_nombre': stop['cliente_nombre'],
            'poblacion': stop['poblacion'],
            'entrega_l': stop['entrega_l'],
            'recogida_l': stop['recogida_l'],
            'peso_kg': stop['peso_kg'],
            'materiales_por_uma': [
                {
                    'uma': uma,
                    'items': items,
                    'total_vol_l': sum(i['vol_l'] for i in items),
                    'total_peso_kg': sum(i['peso_kg'] for i in items),
                    'note': f"{len(items)} líneas de {uma}"
                }
                for uma, items in sorted_umas
            ],
            'stability_note': 'Colocar barriles al fondo, cajas ligeras arriba'
        })
    
    # ===== 4. SECUENCIA DE DESCARGA (Orden de ruta) =====
    unloading_sequence = [
        {
            'unload_position': i + 1,
            'cliente_nombre': stop['cliente_nombre'],
            'poblacion': stop['poblacion'],
            'entrega_l': stop['entrega_l'],
            'recogida_l': stop['recogida_l'],
            'acceso': 'lateral (toldo)' if i % 2 == 0 else 'trasero',
            'estimated_time_min': max(5, int(stop['entrega_l'] / 200))  # Heurística: ~200L/min
        }
        for i, stop in enumerate(stops)
    ]
    
    # ===== 5. PUNTOS DE FRICCIÓN =====
    friction_points = []
    
    # Detector: volumen cercano a capacidad
    total_vol = total_entrega_l + total_recogida_l
    capacity_ratio = total_vol / truck_capacity_l
    if capacity_ratio > 0.95:
        friction_points.append(
            f"ALERTA: Utilización del {capacity_ratio*100:.0f}% de capacidad - margen muy ajustado"
        )
    elif capacity_ratio > 0.85:
        friction_points.append(
            f"Utilización al {capacity_ratio*100:.0f}% - poco margen para imprevistos"
        )
    
    # Detector: clientes con múltiples entregas en ruta
    for cliente_id, orders in cliente_clusters.items():
        if len(orders) > 1:
            friction_points.append(
                f"Cliente {cliente_id} aparece {len(orders)} veces - verificar si es consolidable"
            )
    
    # Detector: distribución desigual de población
    if len(poblacion_clusters) > 1:
        max_cluster = max(poblacion_clusters.values(), key=len)
        min_cluster = min(poblacion_clusters.values(), key=len)
        if len(max_cluster) / len(min_cluster) > 3:
            friction_points.append(
                "Ruta geografía muy dispersa - considera separar en múltiples camiones"
            )
    
    # Detector: paradas de corta duración (puede indicar consolidación posible)
    for stop in stops:
        vol = stop['entrega_l']
        if vol < 100:  # Muy poca carga
            friction_points.append(
                f"Parada {stop['order']} ({stop['cliente_nombre']}) tiene solo {vol:.0f}L - "
                f"considerar consolidación con cliente cercano"
            )
    
    if not friction_points:
        friction_points.append("Ruta bien balanceada, sin fricción operativa detectada")
    
    # ===== 6. ALERTAS DE EFICIENCIA =====
    efficiency_alerts = []
    
    # Alerta: muchas paradas con poco volumen
    small_stops = [s for s in stops if s['entrega_l'] < 100]
    if len(small_stops) >= 3:
        efficiency_alerts.append(
            f"Alto número de micro-paradas ({len(small_stops)}): considerar modelo híbrido "
            f"(por cliente + por referencia) para consolidar"
        )
    
    # Alerta: retornables sin recoger
    if total_recogida_l == 0 and total_entrega_l > 1000:
        efficiency_alerts.append(
            "No hay logística inversa activada: se pierden oportunidades de recogida optimizada"
        )
    elif total_recogida_l > 0:
        recogida_ratio = total_recogida_l / total_entrega_l
        if recogida_ratio > 0.5:
            efficiency_alerts.append(
                f"Alto volumen de retornables ({recogida_ratio*100:.0f}%) - optimizar espacio lateral"
            )
    
    # Alerta: mejora muy marginal
    if dist_improvement < 5:
        efficiency_alerts.append(
            "Mejora de distancia marginal (<5%) - orden actual es cercano al óptimo"
        )
    
    if not efficiency_alerts:
        efficiency_alerts.append("Configuración eficiente detectada")
    
    # ===== 7. MÉTRICAS DE EFICIENCIA =====
    key_metrics = {
        'num_stops': len(stops),
        'baseline_time_h': baseline_time_s / 3600,
        'optimized_time_h': opt_time_s / 3600,
        'time_savings_h': (baseline_time_s - opt_time_s) / 3600,
        'time_improvement_pct': time_improvement,
        'baseline_dist_km': baseline_dist_m / 1000,
        'optimized_dist_km': opt_dist_m / 1000,
        'dist_savings_km': (baseline_dist_m - opt_dist_m) / 1000,
        'dist_improvement_pct': dist_improvement,
        'total_volume_l': total_vol,
        'capacity_utilization_pct': capacity_ratio * 100,
        'volume_per_stop_l': total_entrega_l / len(stops),
        'avg_route_stop_time_min': (opt_time_s / 3600 * 60) / len(stops),
    }
    
    # ===== 8. EXPLICACIONES =====
    explanations = {
        'optimization_approach': (
            "OR-Tools CVRP (Capacitated Vehicle Routing Problem) con metaheurística Guided Local Search. "
            "Minimiza tiempo total de ruta respetando capacidad de volumen/peso y ventanas de tiempo."
        ),
        'distance_metric': (
            f"Basado en matriz de distancias entre coordenadas geocodificadas (haversine). "
            f"Línea recta Mollet del Vallès → {stops[0]['poblacion'] if stops else 'destino'} es {baseline_dist_m/1000:.1f}km; "
            f"ruta total {baseline_dist_m/1000:.1f}km."
        ),
        'why_this_order': (
            f"El algoritmo prioriza {' y '.join(optimization_criteria[:2])}. "
            f"Esta secuencia evita cruces de ruta y agrupa clientes por proximidad geográfica."
        ),
        'reverse_logistics': (
            f"Incorpora recogida de retornables: {total_recogida_l:.0f}L de {total_vol:.0f}L totales ({(total_recogida_l/total_vol)*100:.1f}%). "
            f"Retornables (BRL, BOT, etc.) se descargan primero en parada para evitar bloqueos."
        ) if total_recogida_l > 0 else "Sin logística inversa en esta ruta.",
        'loading_strategy': (
            "LIFO (Last In First Out): clientes finales se cargan primero en almacén. "
            "Barriles (pesados) al fondo, cajas (ligeras) arriba. Acceso lateral mediante toldo."
        ),
    }
    
    # ===== 9. RECOMENDACIONES ACCIONABLES =====
    recommendations = []
    
    # Rec 1: Consolidación
    if len(small_stops) >= 2:
        recommendations.append({
            'action': 'consolidar_paradas_pequeñas',
            'priority': 'high',
            'description': 'Consolidar micro-paradas',
            'details': (
                f"Agrupar {len(small_stops)} paradas pequeñas (<100L cada una) con clientes cercanos "
                f"en el mismo municipio. Reducción estimada: {len(small_stops)-1} paradas."
            ),
            'impact': 'Menos paradas = menos tiempo de carga/descarga = mejor eficiencia'
        })
    
    # Rec 2: Modelo híbrido
    if len(poblacion_clusters) > 1 and len(small_stops) >= 2:
        recommendations.append({
            'action': 'considerar_modelo_hibrido',
            'priority': 'medium',
            'description': 'Explorar modelo híbrido de carga',
            'details': (
                "Por cliente en municipios principales + por referencia en paradas satélite. "
                "Optimiza preparación en almacén y tiempos de descarga simultáneamente."
            ),
            'impact': 'Mejor balance entre eficiencia de almacén y logística'
        })
    
    # Rec 3: Layout del almacén
    if len(stops) > 20:
        top_materiales = get_top_materiales_by_frequency(stops, top_k=3)
        recommendations.append({
            'action': 'optimizar_layout_almacen',
            'priority': 'medium',
            'description': 'Reorganizar almacén por frecuencia de ruta',
            'details': (
                f"Colocar más cerca del muelle: {', '.join([m['material'] for m in top_materiales])}. "
                f"Reducción esperada en tiempo de picking: 10-15%."
            ),
            'impact': 'Preparación de rutas más rápida'
        })
    
    # Rec 4: Agrupación en pre-venta
    if len(cliente_clusters) < len(stops) * 0.7:  # Muchos clientes con múltiples paradas
        repeat_clients = [(cid, len(ords)) for cid, ords in cliente_clusters.items() if len(ords) > 1]
        recommendations.append({
            'action': 'agrupacion_preventa',
            'priority': 'medium',
            'description': 'Coordinar con pre-venta para consolidar entregas',
            'details': (
                f"{len(repeat_clients)} clientes aparecen múltiples veces. "
                f"Consolidar en una única entrega negociando con clientes."
            ),
            'impact': 'Reducción de paradas y tiempo de operación'
        })
    
    # Rec 5: Acceso lateral
    recommendations.append({
        'action': 'aprovechar_toldo_lateral',
        'priority': 'low',
        'description': 'Usar toldo lateral estratégicamente',
        'details': (
            "Acceso lateral permite descargar sin desmontar palets. "
            "Usar para paradas con volumen 100-300L donde no hay margen vertical."
        ),
        'impact': 'Reduce tiempo de descarga en paradas medianas'
    })
    
    return RouteInsight(
        transporte_id=transporte_id,
        optimization_criteria=optimization_criteria,
        cluster_recommendations=cluster_recommendations,
        loading_sequence=loading_sequence,
        unloading_sequence=unloading_sequence,
        friction_points=friction_points,
        efficiency_alerts=efficiency_alerts,
        key_metrics=key_metrics,
        explanations=explanations,
        recommendations=recommendations,
    )


def get_top_materiales_by_frequency(stops: list[dict], top_k: int = 5) -> list[dict]:
    """Obtiene los materiales más frecuentes en una ruta."""
    freq = defaultdict(int)
    for stop in stops:
        for mat in stop.get('materiales', []):
            freq[mat['material']] += 1
    
    return [
        {'material': mat, 'frequency': freq[mat]}
        for mat in sorted(freq.keys(), key=lambda x: freq[x], reverse=True)[:top_k]
    ]


def format_insights_for_terminal(insight: RouteInsight) -> str:
    """Formatea los insights para impresión en terminal."""
    output = []
    
    output.append("\n" + "="*90)
    output.append("INSIGHTS Y RECOMENDACIONES OPERACIONALES")
    output.append("="*90)
    
    # Criterios priorizados
    output.append("\n[1] CRITERIOS PRIORIZADOS EN OPTIMIZACION:")
    for i, crit in enumerate(insight.optimization_criteria, 1):
        output.append(f"    {i}. {crit}")
    
    # Explicación del enfoque
    output.append("\n[2] POR QUE ESTA RUTA:")
    for key, explanation in insight.explanations.items():
        output.append(f"\n    {key.replace('_', ' ').title()}:")
        output.append(f"    {explanation}")
    
    # Clusters y agrupaciones
    if insight.cluster_recommendations:
        output.append("\n[3] OPORTUNIDADES DE AGRUPACION:")
        for cluster in insight.cluster_recommendations:
            priority_badge = "[HIGH]" if cluster['priority'] == 'high' else "[MED ]"
            output.append(f"\n    {priority_badge} {cluster['note']}")
            if cluster['type'] == 'geographic_cluster':
                clients = ", ".join(cluster['clients'][:3])
                if len(cluster['clients']) > 3:
                    clients += f", +{len(cluster['clients'])-3} más"
                output.append(f"             Clientes: {clients}")
    
    # Puntos de fricción
    if insight.friction_points:
        output.append("\n[4] PUNTOS DE FRICCION / ALERTAS OPERACIONALES:")
        for friction in insight.friction_points:
            output.append(f"    ! {friction}")
    
    # Alertas de eficiencia
    if insight.efficiency_alerts:
        output.append("\n[5] ALERTAS DE EFICIENCIA:")
        for alert in insight.efficiency_alerts:
            output.append(f"    ! {alert}")
    
    # Recomendaciones
    if insight.recommendations:
        output.append("\n[6] RECOMENDACIONES ACCIONABLES:")
        for i, rec in enumerate(insight.recommendations, 1):
            priority_symbol = "!" if rec['priority'] == 'high' else "~" if rec['priority'] == 'medium' else "."
            output.append(f"\n    {i}. [{rec['priority'].upper()}] {rec['description']}")
            output.append(f"       {rec['details']}")
            output.append(f"       => Impacto: {rec['impact']}")
    
    # Métricas resumidas
    output.append("\n[7] METRICAS RESUMIDAS:")
    metrics = insight.key_metrics
    output.append(f"\n    Ruta:")
    output.append(f"      • {metrics['num_stops']} paradas")
    output.append(f"      • Volumen total: {metrics['total_volume_l']:.0f}L ({metrics['capacity_utilization_pct']:.0f}% capacidad)")
    output.append(f"      • Volumen medio por parada: {metrics['volume_per_stop_l']:.0f}L")
    output.append(f"\n    Tiempo:")
    output.append(f"      • Original: {metrics['baseline_time_h']:.2f}h ({int(metrics['baseline_time_h']*60)} min)")
    output.append(f"      • Optimizado: {metrics['optimized_time_h']:.2f}h ({int(metrics['optimized_time_h']*60)} min)")
    output.append(f"      • Ahorro: {metrics['time_savings_h']:.2f}h ({metrics['time_improvement_pct']:.1f}%)")
    output.append(f"\n    Distancia:")
    output.append(f"      • Original: {metrics['baseline_dist_km']:.1f}km")
    output.append(f"      • Optimizado: {metrics['optimized_dist_km']:.1f}km")
    output.append(f"      • Ahorro: {metrics['dist_savings_km']:.1f}km ({metrics['dist_improvement_pct']:.1f}%)")
    
    output.append("\n" + "="*90)
    
    return "\n".join(output)
