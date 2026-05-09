"""Tests del pipeline ETL."""
from __future__ import annotations

import pandas as pd
import pytest

from src import config, etl


# ---- Fixtures ---------------------------------------------------------------

@pytest.fixture(scope="module")
def canonical() -> pd.DataFrame:
    return etl.build_canonical(persist=False)


# ---- Smoke / acceptance -----------------------------------------------------

def test_canonical_has_minimum_rows(canonical: pd.DataFrame):
    assert len(canonical) >= 7000, f"esperado >= 7000 filas, recibido {len(canonical)}"


def test_canonical_no_null_critical(canonical: pd.DataFrame):
    for col in ("cliente_id", "transporte", "ruta", "volumen_total_l", "peso_total_kg"):
        assert canonical[col].notna().all(), f"{col} contiene nulls"


def test_canonical_volumen_positivo(canonical: pd.DataFrame):
    assert (canonical["volumen_total_l"] > 0).all(), \
        "alguna entrega tiene volumen_total_l <= 0"


def test_canonical_peso_no_negativo(canonical: pd.DataFrame):
    assert (canonical["peso_total_kg"] >= 0).all()


def test_pct_retornable_en_rango(canonical: pd.DataFrame):
    assert canonical["pct_retornable"].between(0.0, 1.0 + 1e-9).all()


# ---- Caso conocido a mano ---------------------------------------------------

def test_caso_transporte_11420136_cliente_9100696143(canonical: pd.DataFrame):
    """Las 3 líneas conocidas de este (transporte, cliente) deben colapsar
    a una única entrega con volumen estrictamente positivo y 3 lineas."""
    sub = canonical[
        (canonical["transporte"] == 11420136)
        & (canonical["cliente_id"] == 9100696143)
    ]
    assert len(sub) == 1, f"esperaba 1 entrega, hay {len(sub)}"
    fila = sub.iloc[0]
    assert fila["n_lineas"] == 3
    assert fila["volumen_total_l"] > 0
    assert fila["peso_total_kg"] > 0
    assert fila["cliente_nombre"] == "LOS TERESITOS"
    assert fila["poblacion"] == "MONTCADA I REIXAC"  # NBSP limpiado


# ---- Limpieza de strings y CP ----------------------------------------------

def test_cp_padded_a_5_digitos(canonical: pd.DataFrame):
    bad = canonical["cp"].apply(lambda x: not (isinstance(x, str) and len(x) == 5 and x.isdigit()))
    assert not bad.any(), f"{bad.sum()} CPs no son strings de 5 dígitos"


def test_no_nbsp_en_strings(canonical: pd.DataFrame):
    for col in ("poblacion", "cliente_nombre", "calle"):
        contains_nbsp = canonical[col].astype(str).str.contains("\xa0").any()
        assert not contains_nbsp, f"NBSP encontrado en {col}"


# ---- Retornables ------------------------------------------------------------

def test_retornables_marca_uma_obvias():
    """Una caja simple sin keyword no debe marcarse retornable; un BRL sí."""
    df = pd.DataFrame({
        "uma": ["CAJ", "BRL", "UN"],
        "denominacion": ["BONKA CAFE 1KG", "ESTRELLA DAMM BARRIL 30L", "VOLDOR"],
        "volumen_total_l": [10.0, 30.0, 1.0],
    })
    out = etl.mark_retornables(df)
    assert out["retornable"].tolist() == [False, True, False]
    assert out["volumen_retornable_l"].tolist() == [0.0, 30.0, 0.0]


def test_retornables_keyword_envase():
    df = pd.DataFrame({
        "uma": ["CAJ"],
        "denominacion": ["ENVASE VACIO 1L"],
        "volumen_total_l": [5.0],
    })
    out = etl.mark_retornables(df)
    assert out["retornable"].tolist() == [True]


# ---- Enriquecimiento ZM040 -------------------------------------------------

def test_enrich_uses_default_when_no_zm040_match():
    detalle = pd.DataFrame({
        "material": ["ZZZNOEXISTE"],
        "uma": ["BRL"],
        "cantidad": [2],
        "denominacion": ["BARRIL TEST"],
    })
    zm040 = pd.DataFrame({
        "Material": [], "UMA": [], "Volumen": [], "UV": [],
        "Peso bruto": [], "Contador": [],
    })
    out = etl.enrich_with_zm040(detalle, zm040)
    assert out["volumen_total_l"].iloc[0] == 2 * config.UM_DEFAULT_VOLUMEN_L["BRL"]
    assert out["source_dim"].iloc[0] == "uma_default"


def test_enrich_uses_direct_match_when_present():
    detalle = pd.DataFrame({
        "material": ["MATX"],
        "uma": ["CAJ"],
        "cantidad": [3],
        "denominacion": ["X"],
    })
    zm040 = pd.DataFrame({
        "Material": ["MATX"], "UMA": ["CAJ"], "Volumen": [10.0], "UV": ["L"],
        "Peso bruto": [4.0], "Contador": [1.0],
    })
    out = etl.enrich_with_zm040(detalle, zm040)
    assert out["volumen_total_l"].iloc[0] == pytest.approx(30.0)
    assert out["peso_total_kg"].iloc[0] == pytest.approx(12.0)
    assert out["source_dim"].iloc[0] == "zm040_direct"


def test_enrich_scales_via_contador():
    """Si 1 CAJ = 7.524 DM3 y 1 PAL = 168 CAJ, se infiere PAL = 1264.0 L."""
    detalle = pd.DataFrame({
        "material": ["MATY"],
        "uma": ["PAL"],
        "cantidad": [1],
        "denominacion": ["Y"],
    })
    zm040 = pd.DataFrame({
        "Material": ["MATY", "MATY"],
        "UMA":      ["CAJ",  "PAL"],
        "Volumen":  [7.524,  0.0],
        "UV":       ["DM3",  None],
        "Peso bruto": [2.18, 0.0],
        "Contador": [1.0,    168.0],
    })
    out = etl.enrich_with_zm040(detalle, zm040)
    assert out["volumen_total_l"].iloc[0] == pytest.approx(7.524 * 168, rel=1e-6)
    assert out["source_dim"].iloc[0] == "zm040_scaled"


# ---- Schema esperado --------------------------------------------------------

EXPECTED_COLUMNS = {
    "fecha", "transporte", "ruta", "repartidor", "repartidor_nombre",
    "entrega_id", "cliente_id", "cliente_nombre", "calle", "cp",
    "poblacion", "zona_dd",
    "volumen_total_l", "peso_total_kg", "volumen_retornable_l",
    "n_materiales", "n_lineas", "materiales_json", "pct_retornable",
}


def test_canonical_schema(canonical: pd.DataFrame):
    missing = EXPECTED_COLUMNS - set(canonical.columns)
    assert not missing, f"Faltan columnas en canonical: {missing}"
