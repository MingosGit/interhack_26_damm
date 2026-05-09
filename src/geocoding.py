"""Geocoding de direcciones de cliente vía Nominatim (OpenStreetMap).

Uso:
    python -m src.geocoding              # geocodifica clientes que faltan en caché
    python -m src.geocoding --force      # re-geocodifica todo
    python -m src.geocoding --smoke      # genera HTML con 5 muestras al azar

El caché es incremental: cada éxito se persiste al instante a Parquet, así que
una interrupción no pierde trabajo y una segunda ejecución sólo procesa los
pendientes.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from geopy.exc import GeocoderServiceError, GeocoderTimedOut
from geopy.geocoders import Nominatim
from loguru import logger

from src import config
from src.exceptions import ETLError, GeocodingFailedError


# ---------------------------------------------------------------------------
# Geocoder singleton + rate-limit manual
# ---------------------------------------------------------------------------

@dataclass
class _Throttle:
    """Asegura `min_interval_s` entre requests reales a Nominatim."""
    min_interval_s: float = config.NOMINATIM_MIN_INTERVAL_S
    _last_call: float = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if self._last_call and elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)
        self._last_call = time.monotonic()


_GEOCODER: Nominatim | None = None
_THROTTLE = _Throttle()


def _get_geocoder() -> Nominatim:
    global _GEOCODER
    if _GEOCODER is None:
        _GEOCODER = Nominatim(user_agent=config.NOMINATIM_USER_AGENT, timeout=10)
    return _GEOCODER


# ---------------------------------------------------------------------------
# Schema del caché
# ---------------------------------------------------------------------------

CACHE_COLUMNS = ["cliente_id", "lat", "lng", "status", "query_used", "reason"]


def _empty_cache() -> pd.DataFrame:
    return pd.DataFrame({
        "cliente_id": pd.Series(dtype="int64"),
        "lat": pd.Series(dtype="float64"),
        "lng": pd.Series(dtype="float64"),
        "status": pd.Series(dtype="string"),
        "query_used": pd.Series(dtype="string"),
        "reason": pd.Series(dtype="string"),
    })


def load_geocoding_cache(path: Path | None = None) -> pd.DataFrame:
    """Devuelve el caché actual o un DataFrame vacío si no existe."""
    path = path or config.GEOCODING_PARQUET
    if not path.exists():
        return _empty_cache()
    df = pd.read_parquet(path)
    for col in CACHE_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[CACHE_COLUMNS]


def _persist_cache(df: pd.DataFrame, path: Path | None = None) -> None:
    path = path or config.GEOCODING_PARQUET
    path.parent.mkdir(parents=True, exist_ok=True)
    df.reset_index(drop=True).to_parquet(
        path, engine="pyarrow", compression="snappy", index=False,
    )


# ---------------------------------------------------------------------------
# Direcciones a geocodificar
# ---------------------------------------------------------------------------

def load_unique_addresses() -> pd.DataFrame:
    """Lee la hoja `Direcciones` y devuelve UN registro por cliente_id, ya
    normalizado (CP a 5 dígitos, NBSP fuera, mayúsculas en población)."""
    if not config.HACKATON_XLSX.exists():
        raise ETLError(f"No existe {config.HACKATON_XLSX}")

    df = pd.read_excel(config.HACKATON_XLSX, sheet_name="Direcciones")
    df = df.rename(columns={
        "Cliente": "cliente_id",
        "Nombre 1": "cliente_nombre",
        "Calle": "calle",
        "CP": "cp_int",
        "Población": "poblacion",
    })
    df["cp"] = df["cp_int"].apply(lambda x: f"{int(x):05d}" if pd.notna(x) else None)
    for col in ("calle", "poblacion", "cliente_nombre"):
        df[col] = df[col].astype(str).str.replace("\xa0", " ", regex=False).str.strip()
    df["calle"] = df["calle"].str.title()
    df = df.drop(columns=["cp_int"]).drop_duplicates(subset="cliente_id", keep="first")
    return df[["cliente_id", "cliente_nombre", "calle", "cp", "poblacion"]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Helpers de query
# ---------------------------------------------------------------------------

def build_full_query(calle: str, cp: str, poblacion: str) -> str:
    return f"{calle}, {cp} {poblacion}, Catalunya, España"


def build_cp_query(cp: str, poblacion: str | None = None) -> str:
    if poblacion:
        return f"{cp} {poblacion}, Catalunya, España"
    return f"{cp}, Catalunya, España"


def _in_catalunya(lat: float, lng: float) -> bool:
    bb = config.CATALUNYA_BBOX
    return bb["lat_min"] <= lat <= bb["lat_max"] and bb["lng_min"] <= lng <= bb["lng_max"]


# ---------------------------------------------------------------------------
# Geocoding atómico
# ---------------------------------------------------------------------------

def geocode_address(query: str, *, max_retries: int = 1) -> tuple[float, float] | None:
    """Llama a Nominatim respetando rate-limit. Reintenta una vez por timeout
    o error transitorio. Devuelve (lat, lng) o None si no hay resultado."""
    geocoder = _get_geocoder()
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        _THROTTLE.wait()
        try:
            loc = geocoder.geocode(query, exactly_one=True, country_codes="es")
        except (GeocoderTimedOut, GeocoderServiceError) as exc:
            last_exc = exc
            logger.warning("Nominatim transient error ({}/{}) en '{}': {}",
                           attempt + 1, max_retries + 1, query, exc)
            time.sleep(2 * (attempt + 1))
            continue
        if loc is None:
            return None
        return float(loc.latitude), float(loc.longitude)
    if last_exc is not None:
        raise GeocodingFailedError(f"Nominatim falló para '{query}': {last_exc}")
    return None


def fallback_to_cp_centroid(cp: str, poblacion: str | None = None) -> tuple[float, float] | None:
    """Si la dirección completa falla, intenta sólo CP + población."""
    query = build_cp_query(cp, poblacion)
    return geocode_address(query)


# ---------------------------------------------------------------------------
# Geocoding masivo
# ---------------------------------------------------------------------------

def geocode_all(
    addresses: pd.DataFrame,
    *,
    force: bool = False,
    cache_path: Path | None = None,
    persist_every: int = 10,
) -> pd.DataFrame:
    """Geocodifica el DataFrame `addresses` (cliente_id, calle, cp, poblacion).

    Idempotente: si ya hay un cliente con `status='ok'` en el caché y no se
    pasa `force=True`, no se vuelve a llamar a Nominatim.
    Persiste cada `persist_every` resultados Y al final.
    """
    cache_path = cache_path or config.GEOCODING_PARQUET
    cache = load_geocoding_cache(cache_path)
    cache_idx: dict[int, dict] = {int(r.cliente_id): r._asdict()
                                   for r in cache.itertuples(index=False)}

    pending = []
    for r in addresses.itertuples(index=False):
        cid = int(r.cliente_id)
        prev = cache_idx.get(cid)
        prev_status = (prev or {}).get("status") or ""
        if prev and prev_status.startswith("ok") and not force:
            continue
        pending.append(r)

    n_ok_prev = sum(1 for v in cache_idx.values()
                    if str(v.get("status") or "").startswith("ok"))
    n_failed_prev = sum(1 for v in cache_idx.values() if v.get("status") == "failed")
    logger.info("Pendientes: {} / {} (caché previa: {} ok, {} a reintentar)",
                len(pending), len(addresses), n_ok_prev, n_failed_prev)

    written_since_persist = 0
    for i, r in enumerate(pending, 1):
        cid = int(r.cliente_id)
        full_query = build_full_query(r.calle, r.cp, r.poblacion)
        status, lat, lng, query_used, reason = "failed", None, None, full_query, ""
        try:
            res = geocode_address(full_query)
            if res is not None and _in_catalunya(*res):
                lat, lng = res
                status = "ok"
            else:
                if res is not None:
                    reason = f"out_of_bbox({res[0]:.3f},{res[1]:.3f})"
                else:
                    reason = "no_match_full_address"
                cp_query = build_cp_query(r.cp, r.poblacion)
                res2 = geocode_address(cp_query)
                if res2 is not None and _in_catalunya(*res2):
                    lat, lng = res2
                    status = "ok_cp_fallback"
                    query_used = cp_query
                    reason = f"{reason}; cp_fallback_used"
                else:
                    reason = f"{reason}; cp_fallback_failed"
        except GeocodingFailedError as exc:
            reason = f"exception:{exc}"

        cache_idx[cid] = {
            "cliente_id": cid, "lat": lat, "lng": lng,
            "status": status, "query_used": query_used, "reason": reason,
        }
        written_since_persist += 1

        if i % 25 == 0 or i == len(pending):
            ok = sum(1 for v in cache_idx.values() if v.get("status", "").startswith("ok"))
            logger.info("Progreso {}/{} (acumulado ok: {})", i, len(pending), ok)

        if written_since_persist >= persist_every:
            _persist_cache(pd.DataFrame(list(cache_idx.values())), cache_path)
            written_since_persist = 0

    out = pd.DataFrame(list(cache_idx.values()))[CACHE_COLUMNS]
    _persist_cache(out, cache_path)
    return out


# ---------------------------------------------------------------------------
# Smoke test visual
# ---------------------------------------------------------------------------

def smoke_test_html(n: int = 5, seed: int = 42, out_path: Path | None = None) -> Path:
    """Plotea N muestras random + el depot en un HTML Folium para inspección."""
    import folium

    out_path = out_path or config.GEOCODING_SMOKE_HTML
    cache = load_geocoding_cache()
    ok = cache[cache["status"].str.startswith("ok", na=False)].dropna(subset=["lat", "lng"])
    if len(ok) == 0:
        raise GeocodingFailedError("Caché vacío o sin geocodings exitosos")

    sample = ok.sample(min(n, len(ok)), random_state=seed)

    m = folium.Map(location=[config.DEPOT_LAT, config.DEPOT_LNG], zoom_start=10,
                   tiles="OpenStreetMap")
    folium.Marker(
        [config.DEPOT_LAT, config.DEPOT_LNG],
        tooltip=config.DEPOT_NAME,
        icon=folium.Icon(color="red", icon="industry", prefix="fa"),
    ).add_to(m)

    addrs = load_unique_addresses().set_index("cliente_id")
    for r in sample.itertuples(index=False):
        nombre = addrs.loc[r.cliente_id, "cliente_nombre"] if r.cliente_id in addrs.index else str(r.cliente_id)
        folium.Marker(
            [r.lat, r.lng],
            tooltip=f"{nombre} ({r.cliente_id})",
            popup=f"{r.query_used}<br>status={r.status}",
        ).add_to(m)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_path))
    logger.info("Smoke test guardado en {}", out_path)
    return out_path


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(description="Geocoding de Direcciones (Nominatim).")
    parser.add_argument("--force", action="store_true", help="Re-geocodifica todo")
    parser.add_argument("--smoke", action="store_true",
                        help="Sólo genera HTML con muestra del caché actual")
    parser.add_argument("--limit", type=int, default=None,
                        help="Máximo de clientes a procesar (debug)")
    args = parser.parse_args()

    if args.smoke:
        smoke_test_html()
        return

    addrs = load_unique_addresses()
    if args.limit:
        addrs = addrs.head(args.limit)

    cache = geocode_all(addrs, force=args.force)
    n_total = len(addrs)
    n_ok = (cache["status"].fillna("").str.startswith("ok") & cache["cliente_id"].isin(addrs["cliente_id"])).sum()
    logger.info("Cobertura final: {}/{} ({:.1f}%)", n_ok, n_total, 100 * n_ok / n_total if n_total else 0)


if __name__ == "__main__":
    _main()
