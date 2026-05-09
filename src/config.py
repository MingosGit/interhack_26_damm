"""Constantes globales del proyecto Damm Smart Truck."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "cache"

HACKATON_XLSX = DATA_DIR / "Hackaton.xlsx"
ZM040_XLSX = DATA_DIR / "ZM040.XLSX"
LAYOUT_XLSX = DATA_DIR / "Layout Mollet.xlsx"
HORARIOS_XLSX = DATA_DIR / "Horarios Entrega.XLSX"

CANONICAL_PARQUET = CACHE_DIR / "canonical.parquet"
DATA_QUALITY_REPORT = CACHE_DIR / "data_quality_report.txt"

DEPOT_LAT = 41.5408
DEPOT_LNG = 2.2128
DEPOT_NAME = "DDI Mollet del Vallès"

TRUCKS = {
    "6P":  {"palets": 6, "count": 11, "vol_m3": 14.4, "peso_max_kg": 6500},
    "8P":  {"palets": 8, "count": 4,  "vol_m3": 19.2, "peso_max_kg": 8500},
    "FUR": {"palets": 3, "count": 1,  "vol_m3": 7.2,  "peso_max_kg": 3500},
}

PALET_LARGO_CM = 120
PALET_ANCHO_CM = 80
PALET_ALT_MAX_CM = 200

UM_RETORNABLES = {"BRL", "BOT", "BID"}
KEYWORDS_RETORNABLES = ["RET", "RETOR", "BARRIL", "ENVASE", "VACIO"]
RATIO_RETORNO_DEFECTO = 0.6

UV_TO_LITERS = {
    "L": 1.0,
    "HL": 100.0,
    "DM3": 1.0,
    "M3": 1000.0,
    "ML": 0.001,
    "CL": 0.01,
    "CM3": 0.001,
    "GAL": 3.785,
}

# Volumen por defecto (litros) cuando no hay dimensiones reales en ZM040.
# Estimaciones conservadoras alineadas con el catálogo Damm.
UM_DEFAULT_VOLUMEN_L = {
    "CAJ": 30.0,
    "UN":   1.0,
    "BRL": 50.0,
    "BOT":  1.0,
    "PAK":  5.0,
    "TB":   5.0,
    "TIR":  2.0,
    "PQ":   2.0,
    "BID": 20.0,
    "EST": 10.0,
    "ZPR":  1.0,
}

UM_DEFAULT_PESO_KG = {
    "CAJ": 10.0,
    "UN":   0.5,
    "BRL": 30.0,
    "BOT":  1.0,
    "PAK":  3.0,
    "TB":   3.0,
    "TIR":  1.0,
    "PQ":   1.0,
    "BID": 20.0,
    "EST":  5.0,
    "ZPR":  0.5,
}

DEFAULT_VOLUMEN_L = 5.0
DEFAULT_PESO_KG = 2.0
