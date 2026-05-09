"""Pipeline ETL: construye `cache/canonical.parquet` a partir de los xlsx originales.

Uso:
    python -m src.etl
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd
from loguru import logger

from src import config
from src.exceptions import ETLError


# ---------------------------------------------------------------------------
# 1. Carga raw
# ---------------------------------------------------------------------------

def load_raw() -> dict[str, pd.DataFrame]:
    """Carga todas las hojas relevantes en un dict {nombre: DataFrame}."""
    if not config.HACKATON_XLSX.exists():
        raise ETLError(f"No existe el fichero {config.HACKATON_XLSX}")
    if not config.ZM040_XLSX.exists():
        raise ETLError(f"No existe el fichero {config.ZM040_XLSX}")

    logger.info("Leyendo Hackaton.xlsx ...")
    hack_sheets = pd.read_excel(config.HACKATON_XLSX, sheet_name=None)
    logger.info("Leyendo ZM040.XLSX ...")
    zm040 = pd.read_excel(config.ZM040_XLSX)
    return {**hack_sheets, "ZM040": zm040}


# ---------------------------------------------------------------------------
# 2. Limpieza de Detalle entrega
# ---------------------------------------------------------------------------

_RENAME_DETALLE = {
    "FECHA": "fecha",
    "Transporte": "transporte",
    "Ruta": "ruta",
    "Repartidor": "repartidor",
    "Destinatario mcía.": "repartidor_nombre",
    "Entrega": "entrega_id",
    "Material": "material",
    "Denominación": "denominacion",
    "Cantidad entrega": "cantidad",
    "Un.medida venta": "uma",
    "Destinatario mcía..1": "cliente_id",
    "Nombre 1": "cliente_nombre",
    "Nombre 2": "cliente_nombre_2",
    "Calle": "calle",
    "CP": "cp_int",
    "Población": "poblacion",
    "ZonaTransp": "zona_dd",
    "ZonaTransp.1": "zona_nombre",
}


def clean_detalle_entrega(df: pd.DataFrame) -> pd.DataFrame:
    """Renombra cols, parsea fechas DD/MM/YYYY, normaliza CP, limpia NBSP."""
    df = df.rename(columns=_RENAME_DETALLE).copy()
    df["fecha"] = pd.to_datetime(df["fecha"], format="%d/%m/%Y", errors="coerce")
    if df["fecha"].isna().any():
        raise ETLError("Fechas no parseables en Detalle entrega")

    df["cp"] = df["cp_int"].apply(lambda x: f"{int(x):05d}" if pd.notna(x) else None)
    df = df.drop(columns=["cp_int"])

    for col in ("poblacion", "zona_nombre", "calle", "denominacion",
                "cliente_nombre", "cliente_nombre_2", "repartidor_nombre"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace("\xa0", " ", regex=False).str.strip()

    df["uma"] = df["uma"].astype(str).str.strip().str.upper()
    df["material"] = df["material"].astype(str).str.strip()

    # Las columnas de zona vienen mezcladas (str/NaN/int). Forzar string limpio.
    for col in ("zona_dd", "zona_nombre", "ruta", "repartidor_nombre",
                "cliente_nombre", "cliente_nombre_2", "calle", "poblacion",
                "denominacion"):
        if col in df.columns:
            df[col] = df[col].where(df[col].notna(), "").astype(str)
    return df


# ---------------------------------------------------------------------------
# 3. Enriquecimiento con ZM040
# ---------------------------------------------------------------------------

def _build_zm040_lookup(zm040: pd.DataFrame) -> tuple[dict, dict]:
    """Devuelve (lookup_directo, lookup_referencia).

    - lookup_directo: {(material, uma): (volumen_l, peso_kg)} sólo cuando el
      registro tiene Volumen > 0.
    - lookup_referencia: {material: (uma_ref, volumen_ref_l, contador_ref,
      peso_ref_kg)} usando una fila con Volumen > 0 (preferentemente CAJ, luego
      cualquier otra) para extrapolar a otras UMAs vía Contador.
    """
    z = zm040.rename(columns={
        "Material": "material",
        "UMA": "uma",
        "Volumen": "volumen",
        "UV": "uv",
        "Peso bruto": "peso",
        "Contador": "contador",
    }).copy()
    z["material"] = z["material"].astype(str).str.strip()
    z["uma"] = z["uma"].astype(str).str.strip().str.upper()
    z["uv"] = z["uv"].astype(str).str.strip().str.upper()
    z["volumen_l"] = z.apply(
        lambda r: float(r["volumen"]) * config.UV_TO_LITERS.get(r["uv"], 0.0)
        if pd.notna(r["volumen"]) and pd.notna(r["uv"]) else 0.0,
        axis=1,
    )
    z["peso_kg"] = z["peso"].fillna(0.0).astype(float)
    z["contador"] = z["contador"].fillna(1.0).astype(float).replace(0.0, 1.0)

    direct: dict[tuple[str, str], tuple[float, float]] = {}
    for _, r in z.iterrows():
        if r["volumen_l"] > 0 or r["peso_kg"] > 0:
            direct[(r["material"], r["uma"])] = (r["volumen_l"], r["peso_kg"])

    ref: dict[str, tuple[str, float, float, float]] = {}
    # Preferimos referencia con UMA = CAJ, después PAL, después cualquier otra.
    for preferred in ("CAJ", "PAL", None):
        for _, r in z.iterrows():
            if r["volumen_l"] <= 0:
                continue
            if preferred is not None and r["uma"] != preferred:
                continue
            ref.setdefault(
                r["material"],
                (r["uma"], r["volumen_l"], r["contador"], r["peso_kg"]),
            )
    return direct, ref


def enrich_with_zm040(df: pd.DataFrame, zm040: pd.DataFrame) -> pd.DataFrame:
    """Calcula volumen y peso por línea con fallback en cascada.

    Estrategia:
        1. Match directo (material, uma) con Volumen > 0.
        2. Match indirecto: usa otra UMA del mismo material con dato y
           reescala con Contador.
        3. Default por UMA (config.UM_DEFAULT_VOLUMEN_L / PESO_KG).
        4. Default genérico (config.DEFAULT_VOLUMEN_L / PESO_KG).
    """
    direct, ref = _build_zm040_lookup(zm040)
    contador_lookup: dict[tuple[str, str], float] = {}
    for _, r in zm040.rename(columns={
        "Material": "material", "UMA": "uma", "Contador": "contador"
    }).iterrows():
        m = str(r["material"]).strip()
        u = str(r["uma"]).strip().upper()
        c = float(r["contador"]) if pd.notna(r["contador"]) else 1.0
        if c <= 0:
            c = 1.0
        contador_lookup[(m, u)] = c

    fallback_counter: Counter = Counter()
    no_match_log: list[dict] = []

    vol_unit_l: list[float] = []
    peso_unit_kg: list[float] = []
    source_tag: list[str] = []

    for material, uma in zip(df["material"].values, df["uma"].values):
        key = (material, uma)
        if key in direct:
            v, p = direct[key]
            if v <= 0:
                v_default = config.UM_DEFAULT_VOLUMEN_L.get(uma, config.DEFAULT_VOLUMEN_L)
                v = v_default
                fallback_counter[("uma_default_vol", uma)] += 1
            if p <= 0:
                p_default = config.UM_DEFAULT_PESO_KG.get(uma, config.DEFAULT_PESO_KG)
                p = p_default
                fallback_counter[("uma_default_peso", uma)] += 1
            vol_unit_l.append(v)
            peso_unit_kg.append(p)
            source_tag.append("zm040_direct")
            continue

        if material in ref:
            uma_ref, vol_ref_l, contador_ref, peso_ref_kg = ref[material]
            contador_target = contador_lookup.get((material, uma), 1.0)
            scale = contador_target / contador_ref if contador_ref > 0 else 1.0
            v = vol_ref_l * scale
            p = peso_ref_kg * scale if peso_ref_kg > 0 else \
                config.UM_DEFAULT_PESO_KG.get(uma, config.DEFAULT_PESO_KG)
            if v <= 0:
                v = config.UM_DEFAULT_VOLUMEN_L.get(uma, config.DEFAULT_VOLUMEN_L)
                fallback_counter[("ref_no_vol", uma)] += 1
            else:
                fallback_counter[("zm040_scaled", uma)] += 1
            vol_unit_l.append(v)
            peso_unit_kg.append(p)
            source_tag.append("zm040_scaled")
            continue

        v = config.UM_DEFAULT_VOLUMEN_L.get(uma, config.DEFAULT_VOLUMEN_L)
        p = config.UM_DEFAULT_PESO_KG.get(uma, config.DEFAULT_PESO_KG)
        vol_unit_l.append(v)
        peso_unit_kg.append(p)
        source_tag.append("uma_default")
        fallback_counter[("uma_default_full", uma)] += 1
        no_match_log.append({"material": material, "uma": uma})

    df = df.copy()
    df["volumen_unit_l"] = vol_unit_l
    df["peso_unit_kg"] = peso_unit_kg
    df["source_dim"] = source_tag
    df["volumen_total_l"] = df["volumen_unit_l"] * df["cantidad"].astype(float)
    df["peso_total_kg"] = df["peso_unit_kg"] * df["cantidad"].astype(float)

    df.attrs["fallback_counter"] = fallback_counter
    df.attrs["no_match_sample"] = no_match_log[:50]
    return df


# ---------------------------------------------------------------------------
# 4. Marcado de retornables
# ---------------------------------------------------------------------------

def mark_retornables(df: pd.DataFrame) -> pd.DataFrame:
    """Añade `retornable` (bool) y `volumen_retornable_l` por línea."""
    df = df.copy()
    pattern = "|".join(config.KEYWORDS_RETORNABLES)
    by_uma = df["uma"].isin(config.UM_RETORNABLES)
    by_kw = df["denominacion"].fillna("").str.contains(pattern, case=False, regex=True)
    df["retornable"] = (by_uma | by_kw).astype(bool)
    df["volumen_retornable_l"] = df["volumen_total_l"].where(df["retornable"], 0.0)
    return df


# ---------------------------------------------------------------------------
# 5. Agregación a nivel entrega-cliente
# ---------------------------------------------------------------------------

_AGG_KEYS = ["fecha", "transporte", "ruta", "repartidor", "repartidor_nombre",
             "entrega_id", "cliente_id", "cliente_nombre",
             "calle", "cp", "poblacion", "zona_dd"]


def aggregate_by_entrega(df: pd.DataFrame) -> pd.DataFrame:
    """Agrupa de líneas a nivel de entrega.

    Una entrega (`entrega_id`) corresponde a un cliente atendido en un
    transporte concreto. Mantenemos las columnas descriptivas como claves para
    evitar perder información de dirección/zona.
    """
    def _materiales_blob(g: pd.DataFrame) -> str:
        items = [
            {
                "material": r.material,
                "denominacion": r.denominacion,
                "cantidad": int(r.cantidad),
                "uma": r.uma,
                "vol_l": round(float(r.volumen_total_l), 3),
                "peso_kg": round(float(r.peso_total_kg), 3),
                "retornable": bool(r.retornable),
            }
            for r in g.itertuples(index=False)
        ]
        return json.dumps(items, ensure_ascii=False)

    grouped = df.groupby(_AGG_KEYS, dropna=False, sort=False)
    agg = grouped.agg(
        volumen_total_l=("volumen_total_l", "sum"),
        peso_total_kg=("peso_total_kg", "sum"),
        volumen_retornable_l=("volumen_retornable_l", "sum"),
        n_materiales=("material", "nunique"),
        n_lineas=("material", "size"),
    ).reset_index()

    materiales = grouped.apply(_materiales_blob, include_groups=False).rename("materiales_json")
    agg = agg.merge(materiales.reset_index(), on=_AGG_KEYS, how="left")

    agg["pct_retornable"] = (
        agg["volumen_retornable_l"] / agg["volumen_total_l"].replace(0.0, pd.NA)
    ).fillna(0.0).astype(float)
    return agg


# ---------------------------------------------------------------------------
# 6. Pipeline + reporte
# ---------------------------------------------------------------------------

def _write_quality_report(
    raw_lines: int,
    canonical: pd.DataFrame,
    fallback_counter: Counter,
    no_match_sample: list[dict],
    path: Path,
) -> None:
    direct = sum(v for k, v in fallback_counter.items() if k[0] == "zm040_direct")
    direct = direct or (raw_lines - sum(fallback_counter.values()))
    scaled = sum(v for k, v in fallback_counter.items() if k[0] == "zm040_scaled")
    uma_default = sum(v for k, v in fallback_counter.items()
                      if k[0] in ("uma_default_full", "uma_default_vol"))
    pct_real = 100 * (raw_lines - uma_default - scaled) / raw_lines if raw_lines else 0.0
    pct_real_or_scaled = 100 * (raw_lines - uma_default) / raw_lines if raw_lines else 0.0

    top_unmatched = Counter()
    for k, v in fallback_counter.items():
        if k[0] in ("uma_default_full", "uma_default_vol"):
            top_unmatched[k[1]] += v

    ret_breakdown = canonical["pct_retornable"].describe()

    lines = [
        "DATA QUALITY REPORT — canonical.parquet",
        "=" * 60,
        f"Líneas de Detalle entrega procesadas: {raw_lines}",
        f"Entregas únicas (filas en canonical): {len(canonical)}",
        f"Transportes únicos: {canonical['transporte'].nunique()}",
        f"Clientes únicos: {canonical['cliente_id'].nunique()}",
        f"Rutas únicas: {canonical['ruta'].nunique()}",
        f"Rango fechas: {canonical['fecha'].min().date()} → {canonical['fecha'].max().date()}",
        "",
        "Cobertura volumétrica (sobre líneas raw):",
        f"  match directo ZM040 (Volumen > 0): {pct_real:6.2f}%",
        f"  match directo + escalado vía Contador: {pct_real_or_scaled:6.2f}%",
        f"  default por UMA: {100 - pct_real_or_scaled:6.2f}%",
        "",
        "Top UMAs sin dimensiones reales (resueltas por default):",
    ]
    for uma, n in top_unmatched.most_common(10):
        lines.append(f"  {uma:<6} {n}")

    lines += [
        "",
        f"Volumen total agregado (litros): {canonical['volumen_total_l'].sum():,.0f}",
        f"Peso total agregado (kg): {canonical['peso_total_kg'].sum():,.0f}",
        f"Volumen retornable agregado (litros): {canonical['volumen_retornable_l'].sum():,.0f}",
        f"Ratio retornable global: {100 * canonical['volumen_retornable_l'].sum() / canonical['volumen_total_l'].sum():.2f}%",
        "",
        "Distribución pct_retornable por entrega:",
        ret_breakdown.to_string(),
        "",
        "Muestra de pares (material, uma) sin dimensiones (top 20):",
    ]
    seen = set()
    for row in no_match_sample:
        k = (row["material"], row["uma"])
        if k in seen:
            continue
        seen.add(k)
        lines.append(f"  {row['material']:<10} {row['uma']}")
        if len(seen) >= 20:
            break

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_canonical(persist: bool = True) -> pd.DataFrame:
    """Pipeline completo. Devuelve el DataFrame canónico y, opcionalmente, lo
    persiste a Parquet en `cache/canonical.parquet`.
    """
    raw = load_raw()
    detalle = raw["Detalle entrega"]
    zm040 = raw["ZM040"]

    logger.info("Limpiando Detalle entrega ({} filas)...", len(detalle))
    detalle = clean_detalle_entrega(detalle)

    logger.info("Enriqueciendo con ZM040 ({} materiales)...", zm040["Material"].nunique())
    detalle = enrich_with_zm040(detalle, zm040)

    logger.info("Marcando retornables ...")
    detalle = mark_retornables(detalle)

    logger.info("Agregando a nivel entrega ...")
    canonical = aggregate_by_entrega(detalle)

    fallback_counter = detalle.attrs.get("fallback_counter", Counter())
    no_match_sample = detalle.attrs.get("no_match_sample", [])

    if persist:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        canonical.to_parquet(
            config.CANONICAL_PARQUET, engine="pyarrow", compression="snappy", index=False,
        )
        _write_quality_report(
            raw_lines=len(detalle),
            canonical=canonical,
            fallback_counter=fallback_counter,
            no_match_sample=no_match_sample,
            path=config.DATA_QUALITY_REPORT,
        )
        logger.info("Persistido {} ({} filas)", config.CANONICAL_PARQUET, len(canonical))
        logger.info("Reporte de calidad: {}", config.DATA_QUALITY_REPORT)
    return canonical


if __name__ == "__main__":
    build_canonical(persist=True)
