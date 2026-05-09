"""Matriz de tiempos y distancias por carretera para los camiones de DDI.

Soporta dos proveedores intercambiables:

- **ORS** (OpenRouteService) — endpoint HGV (camión). Requiere `ORS_API_KEY`.
- **OSRM público** (`router.project-osrm.org`) — sin auth, perfil driving.

Selección de proveedor:
    - Variable de entorno `MATRIX_PROVIDER=ors|osrm`.
    - Si no está, se usa ORS cuando hay `ORS_API_KEY`, si no OSRM.

Caché por par `(lat1,lng1,lat2,lng2)` persistido a Parquet, con redondeo a
`COORD_PRECISION_DECIMALS`. Una segunda llamada con coords ya vistas no toca
red. Si el proveedor falla puntualmente para un par, se rellena con haversine
× `HAVERSINE_DETOUR_FACTOR` (loggeado).

Uso:
    python -m src.distance_matrix --smoke 25
"""
from __future__ import annotations

import argparse
import math
import os
import time
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
import requests
from loguru import logger

from src import config
from src.exceptions import MatrixProviderError


# ---------------------------------------------------------------------------
# Tipos auxiliares
# ---------------------------------------------------------------------------

Coord = tuple[float, float]            # (lat, lng)
PairKey = tuple[float, float, float, float]
PairValue = tuple[float, float]        # (time_s, dist_m)


def _round(c: Coord) -> Coord:
    p = config.COORD_PRECISION_DECIMALS
    return (round(c[0], p), round(c[1], p))


def haversine_m(a: Coord, b: Coord) -> float:
    """Distancia great-circle en metros."""
    R = 6_371_008.8
    lat1, lng1 = math.radians(a[0]), math.radians(a[1])
    lat2, lng2 = math.radians(b[0]), math.radians(b[1])
    d_lat = lat2 - lat1
    d_lng = lng2 - lng1
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    return float(2 * R * math.asin(math.sqrt(h)))


# ---------------------------------------------------------------------------
# Caché
# ---------------------------------------------------------------------------

_CACHE_COLS = ["lat1", "lng1", "lat2", "lng2", "time_s", "dist_m", "provider"]


def load_distance_cache(path: Path | None = None) -> dict[PairKey, PairValue]:
    """Devuelve el caché como dict {(lat1,lng1,lat2,lng2): (time_s, dist_m)}.

    Si el parquet no existe, devuelve un dict vacío.
    """
    path = path or config.DISTANCE_MATRIX_PARQUET
    if not path.exists():
        return {}
    df = pd.read_parquet(path)
    out: dict[PairKey, PairValue] = {}
    for r in df.itertuples(index=False):
        out[(float(r.lat1), float(r.lng1), float(r.lat2), float(r.lng2))] = (
            float(r.time_s), float(r.dist_m),
        )
    return out


def _persist_cache(cache: dict[PairKey, PairValue], provider: str,
                   path: Path | None = None) -> None:
    path = path or config.DISTANCE_MATRIX_PARQUET
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"lat1": k[0], "lng1": k[1], "lat2": k[2], "lng2": k[3],
         "time_s": v[0], "dist_m": v[1], "provider": provider}
        for k, v in cache.items()
    ]
    df = pd.DataFrame(rows, columns=_CACHE_COLS)
    df.to_parquet(path, engine="pyarrow", compression="snappy", index=False)


# ---------------------------------------------------------------------------
# Resolución de proveedor
# ---------------------------------------------------------------------------

def resolve_provider(provider: str | None = None) -> str:
    """Política: explícito > MATRIX_PROVIDER > (ORS si hay key, OSRM si no)."""
    if provider:
        p = provider.lower()
        if p not in ("ors", "osrm"):
            raise MatrixProviderError(f"Provider desconocido: {provider}")
        return p
    env = os.environ.get("MATRIX_PROVIDER", "").strip().lower()
    if env in ("ors", "osrm"):
        return env
    if os.environ.get("ORS_API_KEY"):
        return "ors"
    return "osrm"


# ---------------------------------------------------------------------------
# Cliente HTTP con back-off exponencial
# ---------------------------------------------------------------------------

def _http_with_backoff(method: str, url: str, **kwargs) -> requests.Response:
    """GET/POST con reintentos en 429/5xx + back-off exponencial."""
    last_exc: Exception | None = None
    for attempt in range(config.ROUTING_MAX_RETRIES):
        try:
            r = requests.request(method, url, timeout=config.ROUTING_HTTP_TIMEOUT_S, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            wait = 2 ** attempt
            logger.warning("Routing transient ({}/{}) — espero {}s — {}",
                           attempt + 1, config.ROUTING_MAX_RETRIES, wait, exc)
            time.sleep(wait)
            continue
        if r.status_code == 429 or 500 <= r.status_code < 600:
            wait = 2 ** attempt
            logger.warning("Routing HTTP {} ({}/{}) — espero {}s",
                           r.status_code, attempt + 1, config.ROUTING_MAX_RETRIES, wait)
            time.sleep(wait)
            continue
        return r
    raise MatrixProviderError(
        f"Falla persistente {method} {url}: {last_exc or 'HTTP 4xx/5xx'}"
    )


# ---------------------------------------------------------------------------
# Proveedores
# ---------------------------------------------------------------------------

def _query_ors_matrix(coords: list[Coord]) -> dict:
    """OpenRouteService /v2/matrix/driving-hgv (perfil camión)."""
    api_key = os.environ.get("ORS_API_KEY")
    if not api_key:
        raise MatrixProviderError("Falta ORS_API_KEY en el entorno")
    body = {
        "locations": [[lng, lat] for lat, lng in coords],  # ORS = lng,lat
        "metrics": ["duration", "distance"],
    }
    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    r = _http_with_backoff("POST", config.ORS_MATRIX_URL, json=body, headers=headers)
    if r.status_code != 200:
        raise MatrixProviderError(f"ORS HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _query_osrm_matrix(coords: list[Coord]) -> dict:
    """OSRM público /table/v1/driving."""
    pairs = ";".join(f"{lng},{lat}" for lat, lng in coords)  # OSRM = lng,lat
    url = f"{config.OSRM_TABLE_URL}/{pairs}"
    r = _http_with_backoff("GET", url, params={"annotations": "duration,distance"})
    if r.status_code != 200:
        raise MatrixProviderError(f"OSRM HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    if data.get("code") != "Ok":
        raise MatrixProviderError(f"OSRM error: {data.get('code')} {data.get('message','')}")
    return data


def _matrix_from_provider_response(data: dict) -> tuple[np.ndarray, np.ndarray]:
    """Extrae arrays NxN tanto de ORS como de OSRM (mismo schema lógico)."""
    durations = data.get("durations")
    distances = data.get("distances")
    if durations is None or distances is None:
        raise MatrixProviderError("Respuesta sin 'durations' o 'distances'")
    return np.asarray(durations, dtype=float), np.asarray(distances, dtype=float)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def _all_pairs_in_cache(coords: list[Coord], cache: dict[PairKey, PairValue]) -> bool:
    for a in coords:
        for b in coords:
            if (a[0], a[1], b[0], b[1]) not in cache:
                return False
    return True


def _haversine_fill(a: Coord, b: Coord) -> PairValue:
    if a == b:
        return 0.0, 0.0
    dist_m = haversine_m(a, b) * config.HAVERSINE_DETOUR_FACTOR
    time_s = dist_m / config.URBAN_AVG_SPEED_MS
    return time_s, dist_m


def get_matrix(
    coords: Sequence[Coord],
    *,
    provider: str | None = None,
    cache_path: Path | None = None,
    persist: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Devuelve (tiempos_segundos, distancias_metros) NxN para `coords`.

    - Si todos los pares están en caché, no toca red.
    - Si no, llama al proveedor con la lista completa y popula el caché.
    - Pares que el proveedor devuelva como `null/None` se rellenan con
      haversine × `HAVERSINE_DETOUR_FACTOR` (loggeado).
    """
    if not coords:
        raise MatrixProviderError("Lista de coordenadas vacía")
    coords_r = [_round(c) for c in coords]

    cache = load_distance_cache(cache_path)
    n = len(coords_r)
    time_mat = np.zeros((n, n), dtype=float)
    dist_mat = np.zeros((n, n), dtype=float)

    if _all_pairs_in_cache(coords_r, cache):
        logger.info("Matriz {}x{} servida del caché", n, n)
        for i, a in enumerate(coords_r):
            for j, b in enumerate(coords_r):
                t, d = cache[(a[0], a[1], b[0], b[1])]
                time_mat[i, j] = t
                dist_mat[i, j] = d
        return time_mat, dist_mat

    prov = resolve_provider(provider)
    logger.info("Pidiendo matriz {}x{} a provider={}", n, n, prov)
    if prov == "ors":
        data = _query_ors_matrix(list(coords_r))
    else:
        data = _query_osrm_matrix(list(coords_r))

    durations, distances = _matrix_from_provider_response(data)
    if durations.shape != (n, n) or distances.shape != (n, n):
        raise MatrixProviderError(
            f"Dimensiones inesperadas: durations={durations.shape} distances={distances.shape}, esperaba ({n},{n})"
        )

    n_filled = 0
    for i, a in enumerate(coords_r):
        for j, b in enumerate(coords_r):
            t = durations[i, j]
            d = distances[i, j]
            if not (np.isfinite(t) and np.isfinite(d)):
                t, d = _haversine_fill(a, b)
                n_filled += 1
            cache[(a[0], a[1], b[0], b[1])] = (float(t), float(d))
            time_mat[i, j] = t
            dist_mat[i, j] = d
    if n_filled:
        logger.warning("Pares rellenados por haversine: {}", n_filled)

    if persist:
        _persist_cache(cache, prov, cache_path)
    return time_mat, dist_mat


# ---------------------------------------------------------------------------
# Smoke / CLI
# ---------------------------------------------------------------------------

def _smoke(n: int) -> None:
    geo = pd.read_parquet(config.GEOCODING_PARQUET)
    geo_ok = geo[geo["status"].astype(str).str.startswith("ok")].dropna(subset=["lat", "lng"])
    if len(geo_ok) < n:
        logger.warning("Sólo hay {} clientes geocodificados, ajustando n", len(geo_ok))
        n = len(geo_ok)

    sample = geo_ok.sample(n, random_state=42)
    coords: list[Coord] = [(config.DEPOT_LAT, config.DEPOT_LNG)]
    coords += [(float(r.lat), float(r.lng)) for r in sample.itertuples(index=False)]

    t0 = time.monotonic()
    times, dists = get_matrix(coords)
    t1 = time.monotonic() - t0
    logger.info("OK matriz {}x{} en {:.2f}s | tiempo medio depot→cliente: {:.0f}s | dist media: {:.0f} m",
                len(coords), len(coords), t1, times[0, 1:].mean(), dists[0, 1:].mean())

    t0 = time.monotonic()
    _, _ = get_matrix(coords)
    t1 = time.monotonic() - t0
    logger.info("Segunda llamada (debe ser caché): {:.3f}s", t1)


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", type=int, default=0,
                        help="Smoke test con N clientes (incluyendo depot)")
    args = parser.parse_args()
    if args.smoke:
        _smoke(args.smoke)
    else:
        parser.print_help()


if __name__ == "__main__":
    _main()
