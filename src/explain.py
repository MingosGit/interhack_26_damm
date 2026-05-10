"""Módulo de explicabilidad con LLM (Groq/Llama3).

Genera explicaciones en lenguaje natural sobre por qué se eligió una ruta,
distribución de camiones, o empaquetamiento. Usa Groq con Llama 3.1.

**MVP Feature**: Explicaciones multiidioma (ES/EN) del proceso de optimización.
"""
from __future__ import annotations

import os
from typing import Any

from loguru import logger

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("groq no está instalado. Instala con: pip install groq")

from src.vrp_solver import Solution, FleetSolution, Stop


DEFAULT_GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")


# ---------------------------------------------------------------------------
# Cliente Groq
# ---------------------------------------------------------------------------

def _get_groq_client() -> Groq | None:
    """Obtiene cliente Groq desde variable de entorno GROQ_API_KEY.
    
    Retorna None si la API key no está configurada o groq no está instalado.
    """
    if not GROQ_AVAILABLE:
        return None
    
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        logger.warning("GROQ_API_KEY no configurada. Para explicabilidad, ejecuta:")
        logger.warning("  export GROQ_API_KEY='tu_clave_aqui'")
        return None
    
    return Groq(api_key=api_key)


# ---------------------------------------------------------------------------
# Explicación de ruta simple (single truck)
# ---------------------------------------------------------------------------

def explain_route(
    solution: Solution,
    stops: list[Stop],
    language: str = "es",
    max_tokens: int = 500,
) -> str:
    """Explica por qué se eligió esta ruta en lenguaje natural.
    
    **Parámetros**:
    - `solution`: Solución del solver (Solution dataclass)
    - `stops`: Lista de paradas original (antes de optimizar)
    - `language`: "es" o "en"
    - `max_tokens`: Límite de tokens en la respuesta
    
    **Retorna**: Explicación en lenguaje natural (ej: "Se priorizó reducir la distancia...")
    """
    client = _get_groq_client()
    if client is None:
        return _fallback_route_explanation(solution, stops, language)
    
    # Construir contexto
    n_stops = len(solution.ordered_stops)
    total_dist_km = solution.total_distance_m / 1000
    total_time_hms = _format_hms(solution.total_time_s)
    
    # Comparar con baseline si existe
    delta_info = ""
    if solution.raw_solver_output.get("baseline_dist_m"):
        baseline_dist = solution.raw_solver_output["baseline_dist_m"]
        delta_pct = 100 * (solution.total_distance_m - baseline_dist) / baseline_dist if baseline_dist else 0
        delta_info = f"\nMejora vs baseline: {delta_pct:.1f}% (distancia optimizada)"
    
    # Información de carga
    load_info = ""
    if solution.carga_viva_max_l > 0:
        load_info = (
            f"\nPerfil de carga: máximo {solution.carga_viva_max_l:.0f}L en parada "
            f"{solution.pico_parada_idx}, total retornable: {solution.total_retornable_l:.0f}L"
        )
    
    prompt = f"""
Eres un experto en logística de última milla. Explica brevemente por qué el solver eligió esta ruta \
de reparto para {n_stops} clientes.

**Contexto de la ruta**:
- Paradas: {n_stops} clientes
- Distancia total: {total_dist_km:.2f} km
- Tiempo total: {total_time_hms}
- Estado: {solution.status}
{delta_info}
{load_info}

**Paradas en orden de ruta**:
{chr(10).join(f"{i}. {s.cliente_nombre} ({s.poblacion}) - {s.volumen_l:.0f}L" 
              for i, s in enumerate(solution.ordered_stops, 1))}

Explica en máximo 100 palabras qué criterios de optimización se aplicaron (distancia, tiempo, capacidad).
Sé conciso y técnico.

Idioma: {"español" if language == "es" else "english"}
"""
    
    try:
        message = client.chat.completions.create(
            model=DEFAULT_GROQ_MODEL,
            max_tokens=max_tokens,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        explanation = message.choices[0].message.content
        logger.info("Explicación generada por Groq (ruta simple)")
        return explanation
    except Exception as e:
        logger.error("Error al llamar a Groq: {}", e)
        return _fallback_route_explanation(solution, stops, language)


def _fallback_route_explanation(solution: Solution, stops: list[Stop], language: str) -> str:
    """Explicación sin LLM (fallback)."""
    if language == "es":
        return (
            f"✓ Ruta optimizada:\n"
            f"  - {len(solution.ordered_stops)} paradas\n"
            f"  - Distancia: {solution.total_distance_m/1000:.2f} km\n"
            f"  - Tiempo: {_format_hms(solution.total_time_s)}\n"
            f"  - Criterios: minimizar distancia + tiempo, respetar capacidad"
        )
    else:
        return (
            f"✓ Optimized route:\n"
            f"  - {len(solution.ordered_stops)} stops\n"
            f"  - Distance: {solution.total_distance_m/1000:.2f} km\n"
            f"  - Time: {_format_hms(solution.total_time_s)}\n"
            f"  - Criteria: minimize distance + time, respect capacity"
        )


# ---------------------------------------------------------------------------
# Explicación de flota multi-camión (CVRP)
# ---------------------------------------------------------------------------

def explain_fleet(
    solution: FleetSolution,
    stops: list[Stop],
    language: str = "es",
    max_tokens: int = 800,
) -> str:
    """Explica por qué se distribuyeron los clientes de esta forma entre camiones.
    
    **Parámetros**:
    - `solution`: Solución de flota (FleetSolution dataclass)
    - `stops`: Lista de paradas original
    - `language`: "es" o "en"
    - `max_tokens`: Límite de tokens
    
    **Retorna**: Explicación de la distribución multi-camión
    """
    client = _get_groq_client()
    if client is None:
        return _fallback_fleet_explanation(solution, language)
    
    # Contexto de flota
    n_routes = len(solution.routes)
    n_stops_total = sum(len(r) for r in solution.routes)
    total_dist_km = solution.total_distance_m / 1000
    
    # Detalles por ruta
    route_summaries = []
    for i, (route, metrics) in enumerate(zip(solution.routes, solution.route_metrics)):
        vol = sum(s.volumen_l for s in route)
        route_summaries.append(
            f"Camión {i+1}: {len(route)} paradas, {vol:.0f}L, {metrics['distance_m']/1000:.2f}km"
        )
    
    prompt = f"""
Eres un experto en optimización de flotas. Explica por qué el solver CVRP distribuyó \
{n_stops_total} clientes entre {n_routes} camiones de esta forma.

**Contexto de la flota**:
- Total de paradas: {n_stops_total}
- Camiones usados: {n_routes} (de {solution.raw_solver_output.get('n_vehicles_requested', n_routes)} disponibles)
- Distancia total: {total_dist_km:.2f} km
- Tiempo total: {_format_hms(solution.total_time_s)}
- Estado: {solution.status}

**Asignación por camión**:
{chr(10).join(route_summaries)}

**Primera parada de cada ruta**:
{chr(10).join(
    f"Camion {i+1} -> {(route[0].cliente_nombre if route else 'N/A')}"
    for i, route in enumerate(solution.routes)
)}

En máximo 150 palabras, explica:
1. ¿Por qué se usaron {n_routes} camiones?
2. ¿Qué criterios guiaron la asignación (geografía, volumen, equilibrio)?
3. ¿Cuál fue el tradeoff entre utilización de flota y calidad de rutas?

Idioma: {"español" if language == "es" else "english"}
"""
    
    try:
        message = client.chat.completions.create(
            model=DEFAULT_GROQ_MODEL,
            max_tokens=max_tokens,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        explanation = message.choices[0].message.content
        logger.info("Explicación generada por Groq (flota multi-camión)")
        return explanation
    except Exception as e:
        logger.error("Error al llamar a Groq: {}", e)
        return _fallback_fleet_explanation(solution, language)


def _fallback_fleet_explanation(solution: FleetSolution, language: str) -> str:
    """Explicación de flota sin LLM."""
    n_routes = len(solution.routes)
    n_stops = sum(len(r) for r in solution.routes)
    
    if language == "es":
        return (
            f"✓ Flota optimizada:\n"
            f"  - {n_routes} camiones asignados\n"
            f"  - {n_stops} paradas distribuidas\n"
            f"  - Distancia total: {solution.total_distance_m/1000:.2f} km\n"
            f"  - Criterios: minimizar rutas, respetar capacidades, equilibrar carga"
        )
    else:
        return (
            f"✓ Optimized fleet:\n"
            f"  - {n_routes} vehicles assigned\n"
            f"  - {n_stops} stops distributed\n"
            f"  - Total distance: {solution.total_distance_m/1000:.2f} km\n"
            f"  - Criteria: minimize routes, respect capacity, balance load"
        )


# ---------------------------------------------------------------------------
# Explicación de empaquetamiento y carga
# ---------------------------------------------------------------------------

def explain_packaging(
    solution: Solution,
    language: str = "es",
    max_tokens: int = 600,
) -> str:
    """Explica cómo se distribuyó la carga a lo largo de la ruta.
    
    Analiza el perfil de carga viva (carga después de cada entrega + retorno).
    
    **Parámetros**:
    - `solution`: Solución con perfil de carga
    - `language`: "es" o "en"
    - `max_tokens`: Límite de tokens
    
    **Retorna**: Explicación de empaquetamiento y gestión de carga
    """
    client = _get_groq_client()
    if client is None:
        return _fallback_packaging_explanation(solution, language)
    
    # Contexto de carga
    perfil = solution.perfil_carga_l
    pico = solution.carga_viva_max_l
    n_paradas = len(solution.ordered_stops)
    total_vol = sum(s.volumen_l for s in solution.ordered_stops)
    total_ret = solution.total_retornable_l
    
    # Crear mini-gráfico ASCII del perfil (manejar perfil vacío)
    if perfil and len(perfil) > 1:
        max_val = max(perfil) if perfil else 1
        perfil_ascii = _ascii_profile(perfil, max_val, height=5)
    else:
        perfil_ascii = "(No hay perfil disponible)"
    
    # Valores seguros para evitar IndexError si `perfil` está vacío
    perfil_inicio = perfil[0] if perfil else 0
    perfil_final = perfil[-1] if perfil else 0
    pico_idx = solution.pico_parada_idx if getattr(solution, "pico_parada_idx", None) is not None else "N/A"

    prompt = f"""
Eres un experto en logística. Analiza el perfil de carga de esta ruta y \
explica la estrategia de empaquetamiento.

**Contexto de carga**:
- Total de producto: {total_vol:.0f}L
- Total retornable: {total_ret:.0f}L
- Máxima carga viva: {pico:.0f}L (en parada {pico_idx})
- Número de paradas: {n_paradas}

**Perfil de carga (L por parada)**:
{perfil_ascii}

**Valores puntuales del perfil**:
Inicio: {perfil_inicio:.0f}L -> Pico: {pico:.0f}L -> Final: {perfil_final:.0f}L

En máximo 120 palabras, explica:
1. ¿Cuál fue la estrategia de carga (LIFO, FIFO, o mixta)?
2. ¿Por qué se alcanzó el pico de {pico:.0f}L en parada {solution.pico_parada_idx}?
3. ¿Cómo se gestionaron los retornos para mantener capacidad libre?

Idioma: {"español" if language == "es" else "english"}
"""
    
    try:
        message = client.chat.completions.create(
            model=DEFAULT_GROQ_MODEL,
            max_tokens=max_tokens,
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        explanation = message.choices[0].message.content
        logger.info("Explicación generada por Groq (empaquetamiento)")
        return explanation
    except Exception as e:
        logger.error("Error al llamar a Groq: {}", e)
        return _fallback_packaging_explanation(solution, language)


def _fallback_packaging_explanation(solution: Solution, language: str) -> str:
    """Explicación de empaquetamiento sin LLM."""
    if language == "es":
        return (
            f"✓ Empaquetamiento:\n"
            f"  - Volumen total: {sum(s.volumen_l for s in solution.ordered_stops):.0f}L\n"
            f"  - Carga máxima: {solution.carga_viva_max_l:.0f}L\n"
            f"  - Retornable: {solution.total_retornable_l:.0f}L\n"
            f"  - Estrategia: optimizar orden para minimizar carga viva"
        )
    else:
        return (
            f"✓ Packaging:\n"
            f"  - Total volume: {sum(s.volumen_l for s in solution.ordered_stops):.0f}L\n"
            f"  - Peak load: {solution.carga_viva_max_l:.0f}L\n"
            f"  - Returnable: {solution.total_retornable_l:.0f}L\n"
            f"  - Strategy: optimize order to minimize live load"
        )


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _format_hms(seconds: int) -> str:
    """Formatea segundos a HhmMss."""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:01d}h{m:02d}m{s:02d}s"


def _ascii_profile(values: list[float], max_val: float, height: int = 5) -> str:
    """Crea un gráfico ASCII simple del perfil."""
    if not values or max_val == 0:
        return "(vacío)"
    
    # Normalizar a altura
    normalized = [int(round(height * v / max_val)) for v in values]
    
    # Construir líneas
    lines = []
    for level in range(height, 0, -1):
        line = "  "
        for val in normalized:
            line += "#" if val >= level else "."
        lines.append(line)
    
    # Eje X (índices)
    x_axis = "  " + "".join(str(i % 10) for i in range(len(values)))
    lines.append(x_axis)
    
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# API pública de explicabilidad
# ---------------------------------------------------------------------------

def explain_solution(
    solution: Solution,
    stops: list[Stop],
    aspect: str = "all",
    language: str = "es",
) -> dict[str, str]:
    """API unified para explicabilidad de soluciones.
    
    **Parámetros**:
    - `solution`: Solution dataclass
    - `stops`: Lista de paradas original
    - `aspect`: "route", "packaging", "all"
    - `language`: "es" o "en"
    
    **Retorna**: dict con explicaciones
    
    **Ejemplo**:
    ```python
    explanations = explain_solution(sol, stops, aspect="all", language="es")
    print(explanations["route"])
    print(explanations["packaging"])
    ```
    """
    result = {}
    
    if aspect in ("route", "all"):
        result["route"] = explain_route(solution, stops, language)
    
    if aspect in ("packaging", "all"):
        result["packaging"] = explain_packaging(solution, language)
    
    return result


def explain_fleet_solution(
    solution: FleetSolution,
    stops: list[Stop],
    language: str = "es",
) -> dict[str, str]:
    """API para explicabilidad de soluciones multi-camión.
    
    **Parámetros**:
    - `solution`: FleetSolution dataclass
    - `stops`: Lista de paradas original
    - `language`: "es" o "en"
    
    **Retorna**: dict con explicaciones de flota
    
    **Ejemplo**:
    ```python
    explanations = explain_fleet_solution(fleet_sol, stops, language="es")
    print(explanations["fleet"])
    print(explanations["routes"])  # Lista de explicaciones por ruta
    ```
    """
    result = {}
    
    # Explicación general de la flota
    result["fleet"] = explain_fleet(solution, stops, language)
    
    # Explicaciones por cada ruta individual
    result["routes"] = []
    for i, route in enumerate(solution.routes):
        route_explanation = f"Camión {i+1}: {len(route)} paradas" if language == "es" else f"Vehicle {i+1}: {len(route)} stops"
        result["routes"].append(route_explanation)
    
    return result
