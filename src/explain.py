"""Capa de explicabilidad NL con Groq Llama 3.3 70B Versatile.

Tres puntos de entrada:
    - ``explain_route(solution, baseline=None)``
    - ``explain_loading(load, ordered_stops)``
    - ``explain_tradeoffs(comparison)``

Características:
    - Carga la API key desde ``GROQ_API_KEY`` (env o ``.env``).
    - Caché en `cache/explanations.json` indexado por hash SHA-1 del input
      → la demo no consume cuota repetidamente.
    - Timeout duro de ``GROQ_REQUEST_TIMEOUT_S`` segundos. Si Groq no responde
      o falta la API key, devuelve un fallback determinista (template).
    - Lenguaje: español, registro operativo (chófer / jefe de almacén),
      menciona al menos un trade-off.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from loguru import logger

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:                                          # pragma: no cover
    pass

from src import config


# ---------------------------------------------------------------------------
# Prompt base — alineado con el criterio "explicabilidad" del reto
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "Eres un experto en logística de distribución que escribe para el equipo "
    "operativo de DDI (Distribución Directa Integral, grupo Damm). Hablas en "
    "español, registro operativo de chófer y jefe de almacén. Sé concreto, "
    "menciona números reales del input y cita al menos un trade-off. No uses "
    "bullets ni emojis. Máximo 3 párrafos cortos."
)


# ---------------------------------------------------------------------------
# Caché
# ---------------------------------------------------------------------------

_CACHE_LOCK = threading.Lock()


def _cache_path() -> Path:
    return config.EXPLANATIONS_CACHE_JSON


def _load_cache() -> dict[str, str]:
    p = _cache_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Caché de explicaciones corrupto, se regenera")
        return {}


def _save_cache(data: dict[str, str]) -> None:
    p = _cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _hash_input(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Cliente Groq
# ---------------------------------------------------------------------------

_GROQ_CLIENT = None


def _get_client():
    """Devuelve el cliente Groq o None si no hay API key configurada."""
    global _GROQ_CLIENT
    if _GROQ_CLIENT is not None:
        return _GROQ_CLIENT
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return None
    try:
        from groq import Groq
        _GROQ_CLIENT = Groq(api_key=api_key, timeout=config.GROQ_REQUEST_TIMEOUT_S)
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("Groq client init falló: {}", exc)
        return None
    return _GROQ_CLIENT


def _call_groq(prompt: str, *, max_tokens: int = config.GROQ_MAX_TOKENS,
               temperature: float = config.GROQ_TEMPERATURE) -> str | None:
    """Llamada bloqueante a Groq. Devuelve None si timeout o falla."""
    client = _get_client()
    if client is None:
        return None
    try:
        t0 = time.monotonic()
        resp = client.chat.completions.create(
            model=config.GROQ_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = resp.choices[0].message.content
        logger.info("Groq OK ({:.0f} tok aprox) en {:.2f} s",
                    max_tokens, time.monotonic() - t0)
        return text.strip() if text else None
    except Exception as exc:                                    # noqa: BLE001
        logger.warning("Groq falló: {}", exc)
        return None


# ---------------------------------------------------------------------------
# Helpers de entrada
# ---------------------------------------------------------------------------

def _solution_to_payload(solution) -> dict:
    return {
        "n_stops": len(getattr(solution, "ordered_stops", [])),
        "total_time_s": getattr(solution, "total_time_s", 0),
        "total_distance_m": getattr(solution, "total_distance_m", 0),
        "status": getattr(solution, "status", "?"),
        "stops": [
            {
                "k": k,
                "cliente": getattr(s, "cliente_nombre", str(getattr(s, "cliente_id", "?"))),
                "poblacion": getattr(s, "poblacion", ""),
                "vol_l": round(float(getattr(s, "volumen_l", 0.0)), 1),
                "ret_l": round(float(getattr(s, "volumen_retornable_l", 0.0)), 1),
                "ventana": (
                    f"{getattr(s, 'ventana_inicio', None)}–{getattr(s, 'ventana_fin', None)}"
                    if getattr(s, "ventana_inicio", None) is not None else None
                ),
            }
            for k, s in enumerate(getattr(solution, "ordered_stops", []), 1)
        ][:25],   # truncar para que el prompt no se dispare
    }


def _load_to_payload(load) -> dict:
    return {
        "truck_type": getattr(load, "truck_type", "?"),
        "vol_total_l": round(float(getattr(load, "vol_total_l", 0.0)), 1),
        "peso_total_kg": round(float(getattr(load, "peso_total_kg", 0.0)), 1),
        "coherencia_cliente": round(float(getattr(load, "coherencia_cliente", 0.0)), 2),
        "bays": [
            {
                "index": b.index,
                "n_clientes": len({it.cliente_id for it in b.items}),
                "vol_l": round(b.vol_usado_l, 1),
                "peso_kg": round(b.peso_kg, 1),
                "tipos": list({it.tipo_dominante for it in b.items}),
                "clientes": [it.cliente_nombre for it in b.items][:4],
            }
            for b in getattr(load, "bays", []) if b.items
        ],
    }


def _comparison_to_payload(cmp_) -> dict:
    if is_dataclass(cmp_):
        d = asdict(cmp_)
        d["fecha"] = str(d.get("fecha", ""))
        return d
    return dict(cmp_) if hasattr(cmp_, "items") else {"data": str(cmp_)}


# ---------------------------------------------------------------------------
# Entradas públicas
# ---------------------------------------------------------------------------

def _explain_with_cache(payload: dict, prompt: str, fallback: str) -> str:
    h = _hash_input(payload)
    with _CACHE_LOCK:
        cache = _load_cache()
    if h in cache:
        return cache[h]

    text = _call_groq(prompt)
    if not text:
        return fallback

    with _CACHE_LOCK:
        cache = _load_cache()    # re-leer por si otro hilo escribió
        cache[h] = text
        _save_cache(cache)
    return text


def explain_route(solution, baseline: dict | None = None) -> str:
    payload = {"solution": _solution_to_payload(solution), "baseline": baseline}
    base_extra = ""
    if baseline:
        base_extra = (
            f"\nBaseline real (orden por entrega_id): "
            f"distancia {baseline.get('dist_m', 0)/1000:.2f} km, "
            f"tiempo {baseline.get('time_s', 0)/60:.0f} min."
        )
    prompt = (
        "Explica al equipo operativo por qué la propuesta de RUTA es buena.\n"
        f"Status del solver: {payload['solution']['status']}\n"
        f"Paradas: {payload['solution']['n_stops']}\n"
        f"Distancia optimizada: {payload['solution']['total_distance_m']/1000:.2f} km\n"
        f"Tiempo optimizado: {payload['solution']['total_time_s']/60:.0f} min"
        f"{base_extra}\n\n"
        f"Primeras paradas (orden propuesto):\n"
        + "\n".join(
            f"  {st['k']}. {st['cliente']} ({st['poblacion']}) — "
            f"{st['vol_l']} L, retornable {st['ret_l']} L"
            for st in payload['solution']['stops'][:8]
        )
        + "\n\nResponde mencionando concretamente al menos un trade-off del orden propuesto."
    )

    fallback = (
        f"Ruta de {payload['solution']['n_stops']} paradas resuelta como "
        f"{payload['solution']['status']}. Distancia "
        f"{payload['solution']['total_distance_m']/1000:.2f} km y tiempo "
        f"{payload['solution']['total_time_s']/60:.0f} min. El orden agrupa los "
        "clientes por proximidad geográfica desde el depot, lo que reduce "
        "kilómetros pero puede penalizar a clientes con ventanas estrechas si "
        "el chófer adelanta visitas."
    )
    return _explain_with_cache(payload, prompt, fallback)


def explain_loading(load, ordered_stops=None) -> str:
    payload = {"load": _load_to_payload(load)}
    bays_text = "\n".join(
        f"  bahía {b['index']}: {b['vol_l']} L / {b['peso_kg']} kg, "
        f"{b['n_clientes']} cliente(s) [{', '.join(b['tipos'])}] "
        f"— {', '.join(b['clientes'])}"
        for b in payload['load']['bays']
    )
    prompt = (
        "Explica al equipo operativo cómo está cargado el camión.\n"
        f"Camión: {payload['load']['truck_type']}\n"
        f"Volumen total: {payload['load']['vol_total_l']} L · "
        f"Peso: {payload['load']['peso_total_kg']} kg\n"
        f"Coherencia cliente (1.0 = perfecta): {payload['load']['coherencia_cliente']}\n\n"
        f"Asignación a bahías (0 = lado de descarga, primer cliente):\n{bays_text}\n\n"
        "Justifica brevemente por qué el orden de bahías favorece la primera "
        "parada y por qué los productos pesados (BARRIL/PACK) van debajo. "
        "Cita al menos un trade-off."
    )
    fallback = (
        f"El camión {payload['load']['truck_type']} sale con "
        f"{payload['load']['vol_total_l']} L y {payload['load']['peso_total_kg']} "
        "kg. La bahía 0 contiene al primer cliente (lado de descarga, lonas "
        f"laterales abiertas allí). Coherencia de cliente {payload['load']['coherencia_cliente']}: "
        "los pedidos de un mismo cliente se mantienen en bahías contiguas "
        "siempre que es posible. Trade-off: agrupar por cliente puede dejar "
        "huecos verticales que reducen el aprovechamiento del volumen."
    )
    return _explain_with_cache(payload, prompt, fallback)


def explain_tradeoffs(comparison) -> str:
    payload = {"comparison": _comparison_to_payload(comparison)}
    cmp_d = payload["comparison"]
    prompt = (
        "Explica los trade-offs de la propuesta optimizada vs el día real.\n"
        f"Transporte {cmp_d.get('transporte_id', '?')} ({cmp_d.get('fecha','?')}), "
        f"{cmp_d.get('n_paradas', 0)} paradas.\n"
        f"Δ distancia: {cmp_d.get('delta_distancia_pct', 0):+.2f}% "
        f"(real {cmp_d.get('real_distancia_m', 0)/1000:.2f} km → "
        f"opt {cmp_d.get('opt_distancia_m', 0)/1000:.2f} km)\n"
        f"Δ tiempo: {cmp_d.get('delta_tiempo_pct', 0):+.2f}% "
        f"(real {cmp_d.get('real_tiempo_s', 0)/60:.0f} min → "
        f"opt {cmp_d.get('opt_tiempo_s', 0)/60:.0f} min)\n"
        f"Δ movimientos descarga: {cmp_d.get('delta_movimientos_pct', 0):+.2f}%\n"
        f"Δ retornables recogidos: {cmp_d.get('delta_retornables_pp', 0):+.1f} pp "
        f"(real 75% → opt {100*cmp_d.get('opt_pct_retornables_recogidos', 0):.0f}%)\n\n"
        "Sé concreto sobre qué aspecto operativo gana y cuál se sacrifica."
    )
    fallback = (
        f"Frente al orden real, la propuesta reduce distancia un "
        f"{cmp_d.get('delta_distancia_pct', 0):+.1f}%, tiempo un "
        f"{cmp_d.get('delta_tiempo_pct', 0):+.1f}% y los movimientos de "
        f"descarga un {cmp_d.get('delta_movimientos_pct', 0):+.1f}%. La carga "
        "por bahías permite recuperar más retornables porque el espacio liberado "
        "tras cada entrega se asigna explícitamente. Trade-off: el chófer "
        "pierde flexibilidad para reordenar visitas si surge un imprevisto en "
        "ruta."
    )
    return _explain_with_cache(payload, prompt, fallback)
