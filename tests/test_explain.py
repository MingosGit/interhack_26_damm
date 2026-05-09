"""Tests del módulo de explicabilidad (MVP 10). Groq siempre mockeado."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest

from src import explain
from src.vrp_solver import Stop


# ---- Fixtures --------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    """Cada test usa su propio JSON de caché y resetea el cliente Groq."""
    monkeypatch.setattr(explain.config, "EXPLANATIONS_CACHE_JSON",
                         tmp_path / "explanations.json")
    explain._GROQ_CLIENT = None


@dataclass
class _FakeSolution:
    ordered_stops: list = field(default_factory=list)
    total_time_s: int = 0
    total_distance_m: int = 0
    status: str = "OPTIMAL"
    raw_solver_output: dict = field(default_factory=dict)


def _stop(cid: int, vol: float, ret: float = 0, name: str = "X") -> Stop:
    return Stop(cliente_id=cid, lat=0, lng=0, volumen_l=vol, peso_kg=vol*0.5,
                volumen_retornable_l=ret, cliente_nombre=name,
                poblacion="BARCELONA")


@dataclass
class _FakeBay:
    index: int
    items: list = field(default_factory=list)

    @property
    def vol_usado_l(self): return sum(it.volumen_l for it in self.items)
    @property
    def peso_kg(self): return sum(it.peso_kg for it in self.items)


@dataclass
class _FakeBayItem:
    cliente_id: int
    cliente_nombre: str
    volumen_l: float
    peso_kg: float
    tipo_dominante: str = "CAJA"


@dataclass
class _FakeLoad:
    truck_type: str
    vol_total_l: float
    peso_total_kg: float
    coherencia_cliente: float
    bays: list


@dataclass
class _FakeKPI:
    transporte_id: int = 100
    fecha: str = "2026-02-02"
    n_paradas: int = 5
    real_distancia_m: int = 10_000
    real_tiempo_s: int = 3600
    real_n_movimientos_descarga: int = 30
    real_pct_retornables_recogidos: float = 0.75
    opt_distancia_m: int = 8_000
    opt_tiempo_s: int = 3000
    opt_n_movimientos_descarga: int = 20
    opt_pct_retornables_recogidos: float = 1.0
    delta_distancia_pct: float = -20.0
    delta_tiempo_pct: float = -16.7
    delta_movimientos_pct: float = -33.3
    delta_retornables_pp: float = 25.0
    status: str = "OPTIMAL"


def _mock_groq_completion(text: str):
    msg = MagicMock(); msg.message.content = text
    resp = MagicMock(); resp.choices = [msg]
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


# ---- Caché y hashing ------------------------------------------------------

def test_hash_input_is_deterministic():
    a = explain._hash_input({"x": 1, "y": [1, 2, 3]})
    b = explain._hash_input({"y": [1, 2, 3], "x": 1})
    assert a == b


def test_load_cache_returns_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(explain.config, "EXPLANATIONS_CACHE_JSON",
                         tmp_path / "no.json")
    assert explain._load_cache() == {}


def test_save_and_load_cache_roundtrip(tmp_path, monkeypatch):
    p = tmp_path / "exp.json"
    monkeypatch.setattr(explain.config, "EXPLANATIONS_CACHE_JSON", p)
    explain._save_cache({"hash1": "una explicación"})
    assert explain._load_cache() == {"hash1": "una explicación"}


# ---- Cliente Groq ---------------------------------------------------------

def test_get_client_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert explain._get_client() is None


def test_call_groq_returns_none_without_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert explain._call_groq("hola") is None


def test_call_groq_handles_exception(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "xx")
    explain._GROQ_CLIENT = MagicMock()
    explain._GROQ_CLIENT.chat.completions.create.side_effect = RuntimeError("boom")
    assert explain._call_groq("hola") is None


def test_call_groq_returns_text_on_success(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "xx")
    explain._GROQ_CLIENT = _mock_groq_completion("respuesta natural")
    out = explain._call_groq("hola")
    assert out == "respuesta natural"


# ---- Fallback cuando Groq no está disponible ------------------------------

def test_explain_route_returns_fallback_without_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    sol = _FakeSolution(
        ordered_stops=[_stop(1, 100, 50, "Bar X")],
        total_time_s=1800, total_distance_m=12_000,
    )
    out = explain.explain_route(sol)
    assert "1" in out                       # menciona n paradas
    assert "12.00 km" in out or "12.0 km" in out


def test_explain_loading_returns_fallback_without_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    load = _FakeLoad(
        truck_type="6P", vol_total_l=5000, peso_total_kg=2500,
        coherencia_cliente=0.85,
        bays=[_FakeBay(0, [_FakeBayItem(1, "Bar X", 500, 250)])],
    )
    out = explain.explain_loading(load)
    assert "6P" in out
    assert "5000" in out


def test_explain_tradeoffs_returns_fallback_without_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    out = explain.explain_tradeoffs(_FakeKPI())
    assert "20" in out  # cita el delta distancia


# ---- Caché funcional ------------------------------------------------------

def test_explain_route_uses_cache_on_second_call(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "xx")
    explain._GROQ_CLIENT = _mock_groq_completion("primera respuesta")
    sol = _FakeSolution(
        ordered_stops=[_stop(1, 100, 50, "Bar X")],
        total_time_s=1800, total_distance_m=12_000,
    )
    out1 = explain.explain_route(sol)
    out2 = explain.explain_route(sol)
    assert out1 == out2 == "primera respuesta"
    # La segunda llamada NO debe haber tocado a Groq
    assert explain._GROQ_CLIENT.chat.completions.create.call_count == 1


def test_explain_route_calls_groq_with_system_and_user_messages(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "xx")
    explain._GROQ_CLIENT = _mock_groq_completion("ok")
    sol = _FakeSolution(ordered_stops=[_stop(1, 100, 0)],
                         total_time_s=600, total_distance_m=2000)
    explain.explain_route(sol)
    args = explain._GROQ_CLIENT.chat.completions.create.call_args
    msgs = args.kwargs["messages"]
    assert msgs[0]["role"] == "system"
    assert "DDI" in msgs[0]["content"] or "Damm" in msgs[0]["content"]
    assert msgs[1]["role"] == "user"
    assert args.kwargs["model"] == explain.config.GROQ_MODEL


def test_cache_persists_across_load_save(monkeypatch, tmp_path):
    """El JSON de caché es legible tras escribir."""
    monkeypatch.setattr(explain.config, "EXPLANATIONS_CACHE_JSON",
                         tmp_path / "exp.json")
    monkeypatch.setenv("GROQ_API_KEY", "xx")
    explain._GROQ_CLIENT = _mock_groq_completion("respuesta cacheada")
    sol = _FakeSolution(ordered_stops=[_stop(1, 100, 50)],
                         total_time_s=600, total_distance_m=2000)
    explain.explain_route(sol)
    raw = json.loads((tmp_path / "exp.json").read_text())
    assert any("cacheada" in v for v in raw.values())


# ---- Payload truncamiento --------------------------------------------------

def test_solution_payload_truncates_at_25_stops():
    sol = _FakeSolution(
        ordered_stops=[_stop(i, 10) for i in range(50)],
        total_time_s=0, total_distance_m=0,
    )
    payload = explain._solution_to_payload(sol)
    assert len(payload["stops"]) == 25
