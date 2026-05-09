"""Lectura y mapping de Horarios_Entrega → ventanas por cliente.

Mapping `Deudor` → `cliente_id` con cascada:
    1. Match directo (cliente_id == Deudor): ~120/240 clientes.
    2. Match por nombre normalizado (sólo unique): ~19/240.
    3. No mapeado → cliente sin ventana específica.

Para una fecha concreta el módulo devuelve un dict
``{cliente_id: (inicio_s, fin_s)}`` filtrado por día de la semana
correspondiente. Si el cliente tiene varios turnos ese día, se devuelve la
envolvente (min inicio, max fin) — más permisiva, equivalente a "abierto entre
estas horas con descanso interno" (suficiente para la demo).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pandas as pd
from loguru import logger

from src import config


def _norm_name(s: str) -> str:
    s = str(s).upper().strip()
    return "".join(c for c in s if c.isalnum())


def _time_to_seconds(t) -> int | None:
    """Acepta datetime.time, datetime.datetime, timedelta, str 'HH:MM:SS' o NaN."""
    if t is None or (isinstance(t, float) and pd.isna(t)):
        return None
    try:
        if pd.isna(t):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(t, str):
        parts = t.split(":")
        h, m, s = int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0
        return h * 3600 + m * 60 + s
    if isinstance(t, timedelta):
        total = int(t.total_seconds())
        return total % (24 * 3600)
    if isinstance(t, datetime):
        return t.hour * 3600 + t.minute * 60 + t.second
    if isinstance(t, time):
        return t.hour * 3600 + t.minute * 60 + t.second
    raise TypeError(f"Tipo no soportado para hora: {type(t)}={t!r}")


def load_horarios() -> pd.DataFrame:
    """Devuelve el DataFrame de Horarios_Entrega ya normalizado:

    columnas: deudor (int), nombre_norm, dia_semana (1-7), turno (1-2),
    inicio_s, fin_s, cierre_total (bool).
    """
    df = pd.read_excel(config.HORARIOS_XLSX)
    df = df.rename(columns={
        "Deudor": "deudor",
        "Nombre 1": "nombre",
        "Día semana": "dia_semana",
        "Turno": "turno",
        "Horario inicia a": "inicio",
        "Horario termina a": "fin",
        "Cierre Si/No": "cierre",
    })
    df["deudor"] = df["deudor"].astype(int)
    df["nombre_norm"] = df["nombre"].apply(_norm_name)
    df["inicio_s"] = df["inicio"].apply(_time_to_seconds)
    df["fin_s"] = df["fin"].apply(_time_to_seconds)
    df["cierre_total"] = df["cierre"].astype(str).str.upper().str.strip().eq("X")
    return df[["deudor", "nombre_norm", "dia_semana", "turno",
               "inicio_s", "fin_s", "cierre_total"]]


def build_deudor_to_cliente_map(canonical: pd.DataFrame,
                                 horarios: pd.DataFrame) -> dict[int, int]:
    """Construye {deudor: cliente_id} con cascada (id directo → nombre)."""
    canon = canonical[["cliente_id", "cliente_nombre"]].drop_duplicates(subset="cliente_id")
    canon["nombre_norm"] = canon["cliente_nombre"].apply(_norm_name)
    direct_ids = set(canon["cliente_id"].astype(int))

    name_to_ids: dict[str, list[int]] = (
        canon.groupby("nombre_norm")["cliente_id"].apply(list).to_dict()
    )

    mapping: dict[int, int] = {}
    n_direct, n_name, n_unmatched = 0, 0, 0
    for r in horarios.drop_duplicates(subset="deudor")[["deudor", "nombre_norm"]].itertuples(index=False):
        d = int(r.deudor)
        if d in direct_ids:
            mapping[d] = d
            n_direct += 1
            continue
        cands = name_to_ids.get(r.nombre_norm, [])
        if len(cands) == 1:
            mapping[d] = int(cands[0])
            n_name += 1
        else:
            n_unmatched += 1

    total = n_direct + n_name + n_unmatched
    if total:
        logger.info(
            "Mapping Deudor→cliente: directo={} ({}%) | nombre={} ({}%) | sin match={} ({}%)",
            n_direct, round(100 * n_direct / total),
            n_name, round(100 * n_name / total),
            n_unmatched, round(100 * n_unmatched / total),
        )
    return mapping


def _is_open_window(inicio_s: int | None, fin_s: int | None) -> bool:
    """Una ventana "00:00 → 00:00" en Horarios codifica CERRADO ese día.
    Y "00:00 → 23:59:59" significa abierto todo el día. Devolvemos True si
    la ventana es genuinamente útil (acotada y no nula)."""
    if inicio_s is None or fin_s is None:
        return False
    if inicio_s == 0 and fin_s == 0:
        return False
    return True


def windows_for_date(
    fecha: date,
    canonical: pd.DataFrame | None = None,
    horarios: pd.DataFrame | None = None,
) -> dict[int, tuple[int, int]]:
    """Devuelve `{cliente_id: (inicio_s, fin_s)}` válido para la fecha dada.

    - Sólo se devuelven entradas para clientes con ventana acotada (no
      00:00→00:00 ni 00:00→23:59:59 trivial).
    - Si un cliente tiene varios turnos ese día, se aplica envolvente
      `(min inicio, max fin)`.
    - Clientes sin entrada en el dict ⇒ tratar como ventana abierta de jornada.
    """
    if horarios is None:
        horarios = load_horarios()
    if canonical is None:
        canonical = pd.read_parquet(config.CANONICAL_PARQUET)

    dow = fecha.weekday() + 1  # Mon=1 .. Sun=7
    deudor_to_cliente = build_deudor_to_cliente_map(canonical, horarios)

    sub = horarios[horarios["dia_semana"] == dow]
    out: dict[int, tuple[int, int]] = {}
    for deudor, group in sub.groupby("deudor"):
        cid = deudor_to_cliente.get(int(deudor))
        if cid is None:
            continue
        windows = [(r.inicio_s, r.fin_s) for r in group.itertuples(index=False)
                   if _is_open_window(r.inicio_s, r.fin_s)
                   and r.fin_s != config.HORARIO_OPEN_FIN_S]  # excluye triviales
        if not windows:
            continue
        ini = min(w[0] for w in windows)
        fin = max(w[1] for w in windows)
        out[cid] = (int(ini), int(fin))
    return out
