"""Utilities para obtener geometría de rutas siguiendo carreteras.

Soporta proveedores:
- openrouteservice (recomendado, necesita ORS_API_KEY en env o parámetro)
- google directions (necesita GOOGLE_API_KEY en env o parámetro)

Si no se proporciona key o la llamada falla, devuelve None para indicar
que hay que usar la polilínea directa entre puntos.
"""
from __future__ import annotations
import os
from typing import List, Optional
import requests


def _ors_route(coords: List[List[float]], api_key: str) -> Optional[List[List[float]]]:
    # coords: list of [lat, lng]
    url = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"
    # ORS expects [lng, lat]
    body_coords = [[c[1], c[0]] for c in coords]
    payload = {"coordinates": body_coords}
    headers = {"Authorization": api_key, "Content-Type": "application/json"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        geom = data.get("features", [])[0].get("geometry", {}).get("coordinates", [])
        # geom is list of [lng, lat] -> convert to [lat, lng]
        return [[c[1], c[0]] for c in geom]
    except Exception:
        return None


def _decode_polyline(encoded: str) -> List[List[float]]:
    # Decode Google encoded polyline into list of [lat, lng]
    coords: List[List[float]] = []
    index = 0
    lat = 0
    lng = 0
    length = len(encoded)

    while index < length:
        shift = 0
        result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        shift = 0
        result = 0
        while True:
            b = ord(encoded[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        coords.append([lat / 1e5, lng / 1e5])

    return coords


def _google_route(coords: List[List[float]], api_key: str) -> Optional[List[List[float]]]:
    # coords: list of [lat, lng]
    if len(coords) < 2:
        return None
    base = "https://maps.googleapis.com/maps/api/directions/json"
    origin = f"{coords[0][0]},{coords[0][1]}"
    dest = f"{coords[-1][0]},{coords[-1][1]}"
    waypoints = None
    if len(coords) > 2:
        # Google waypoints expect lat,lng|lat,lng ... (without origin/dest)
        pts = [f"{p[0]},{p[1]}" for p in coords[1:-1]]
        waypoints = "|".join(pts)

    params = {"origin": origin, "destination": dest, "key": api_key}
    if waypoints:
        params["waypoints"] = waypoints

    try:
        r = requests.get(base, params=params, timeout=15)
        r.raise_for_status()
        j = r.json()
        if j.get("status") != "OK":
            return None
        overview = j.get("routes", [])[0].get("overview_polyline", {}).get("points")
        if not overview:
            return None
        return _decode_polyline(overview)
    except Exception:
        return None


def get_route_geometry(coords: List[List[float]], provider: str = "ors", api_key: Optional[str] = None) -> Optional[List[List[float]]]:
    """Devuelve una lista de coordenadas [lat, lng] que siguen carreteras.

    - `coords` debe ser lista de puntos en orden de visita [[lat,lng], ...]
    - `provider` puede ser 'ors' o 'google'
    - `api_key` si no se pasa, se lee de ORS_API_KEY o GOOGLE_API_KEY

    Retorna None si no es posible obtener la geometría (fallo o sin key).
    """
    provider = (provider or "ors").lower()
    if provider == "ors":
        key = api_key or os.environ.get("ORS_API_KEY")
        if not key:
            return None
        return _ors_route(coords, key)
    elif provider == "google":
        key = api_key or os.environ.get("GOOGLE_API_KEY")
        if not key:
            return None
        return _google_route(coords, key)
    else:
        return None
