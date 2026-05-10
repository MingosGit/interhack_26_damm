"""
Dashboard interactivo de Streamlit para Damm Smart Truck.
Muestra mapa de ruta, carga, insights y detalles técnicos.
"""

from html import escape
import math
from pathlib import Path
import sys
import streamlit as st
from src.loading_visualization import TruckVisualizer
import folium
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# Añadir src al path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src import config
from src.config import TRUCKS
from src.etl import build_canonical
from src.loading_visualization import visualize_loading_plan
from src.vrp_solver import run_for_fleet, run_for_transporte
import os

# Configuración de rutas para que no falle el "Path"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
logo_cuadrado = os.path.join(BASE_DIR, "assets", "logo_1x1.jpeg") 
logo_horizontal = os.path.join(BASE_DIR, "assets", "logo_alargado.jpeg")
logo_mini = os.path.join(BASE_DIR, "assets", "logo_mini.jpeg") 

st.set_page_config(
    page_title="Damm Smart Truck - Optimizer",
    page_icon="🚚",
    layout="wide",
    initial_sidebar_state="expanded",
)
import base64

# Función para convertir tu logo a formato web (base64)
def get_base64_image(image_path):
    try:
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode()
    except Exception:
        return ""

# Convertimos el logo cuadrado
logo_b64 = get_base64_image(logo_mini)

# Fíjate en la "f" antes de las comillas triples
st.markdown(
    f"""
<style>
    /* 1. FONDO BLANCO TOTAL (App, Sidebar y Cabecera) */
    .stApp, [data-testid="stSidebar"], [data-testid="stHeader"], [data-testid="stToolbar"] {{
        background-color: #ffffff !important;
    }}

    /* 2. SUBIR EL LOGO DE LA BARRA LATERAL (logo_cuadrado) */
    [data-testid="stSidebarHeader"] {{
        padding-top: 0rem !important;
        margin-top: -2.8rem !important;
    }}
    
    [data-testid="stSidebarNav"] {{
        padding-top: 0rem !important;
    }}

    /* BOTÓN PRIMARIO ROJO DAMM */
    div.stButton > button[kind="primary"] {{
        background-color: #E20613 !important;
        color: #FFFFFF !important;
        border-color: #E20613 !important;
    }}
    
    div.stButton > button[kind="primary"]:hover {{
        background-color: #B8050F !important; 
        border-color: #B8050F !important;
    }}

    /* 3. SUBIR EL CONTENIDO PRINCIPAL */
    .block-container {{
        padding-top: 2rem !important;
    }}

    /* 4. ESTILO DAMM PARA MÉTRICAS */
    [data-testid="stMetricValue"] {{
        color: #E20613 !important;
        font-weight: 800;
    }}
    
    div[data-testid="stVerticalBlock"] > div[style*="flex-direction: column;"] {{
        background-color: #ffffff;
        padding: 1rem;
        border: 1px solid #f0f0f0;
        border-radius: 10px;
    }}

    /* 5. LOGO GIRANDO (Adiós a los muñequitos haciendo ejercicio) */
    /* Ocultamos el contenido original de Streamlit */
    [data-testid="stStatusWidget"] > div {{
        display: none !important;
    }}
    
    /* Creamos el nuevo bloque con el logo en base64 y la animación */
    [data-testid="stStatusWidget"]::before {{
        content: "";
        display: block;
        width: 35px;
        height: 35px;
        background-image: url("data:image/jpeg;base64,{logo_b64}");
        background-size: contain;
        background-repeat: no-repeat;
        background-position: center;
        animation: spin 1.2s linear infinite; /* Tiempo que tarda en dar una vuelta completa */
        border-radius: 8px; /* Redondea los bordes si quieres efecto moneda */
    }}

    /* Animación de 360 grados continua */
    @keyframes spin {{
        0% {{ transform: rotate(0deg); }}
        100% {{ transform: rotate(360deg); }}
    }}
</style>
""",
    unsafe_allow_html=True,
)

# 1. LOGO EN EL SIDEBAR (Logo cuadrado agrandado)
# Mostramos el logo cuadrado con un tamaño más grande en el sidebar
if os.path.exists(logo_cuadrado):
    st.sidebar.image(logo_cuadrado, width=90)

# 2. CABECERA PRINCIPAL (Se mantendrá igual pero con fondo blanco total)
col_logo, col_titulo = st.columns([2, 3]) 

with col_logo:
    if os.path.exists(logo_horizontal):
        st.image(logo_horizontal, width=450)
    else:
        st.write("🏢")

with col_titulo:
    # Ajustamos el título para que no tenga márgenes que lo bajen
    st.markdown("<h1 style='margin-top: 0px;'>Damm Smart Truck</h1>", unsafe_allow_html=True)
    st.markdown("<p style='color: #666;'><i>Sistema Inteligente de Optimización de Rutas y Cargas</i></p>", unsafe_allow_html=True)

st.divider()
st.markdown(
    '<p class="sub-header">Optimización de rutas, carga, flota e explainability para demo de hackathon.</p>',
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner=False)
def _load_canonical_data() -> pd.DataFrame:
    if config.CANONICAL_PARQUET.exists():
        return pd.read_parquet(config.CANONICAL_PARQUET)
    return build_canonical(persist=True)


def _group_materials(materiales: list[dict], retornable: bool) -> list[dict]:
    grouped: dict[tuple[str, str, str], dict] = {}
    for item in materiales or []:
        if bool(item.get("retornable", False)) != retornable:
            continue
        key = (
            str(item.get("material", "")).strip(),
            str(item.get("denominacion", "")).strip(),
            str(item.get("uma", "")).strip(),
        )
        if key not in grouped:
            grouped[key] = {
                "material": key[0],
                "denominacion": key[1],
                "uma": key[2],
                "cantidad": 0,
                "vol_l": 0.0,
                "peso_kg": 0.0,
            }
        grouped[key]["cantidad"] += int(item.get("cantidad", 1) or 1)
        grouped[key]["vol_l"] += float(item.get("vol_l", 0) or 0)
        grouped[key]["peso_kg"] += float(item.get("peso_kg", 0) or 0)

    return sorted(grouped.values(), key=lambda x: (x["peso_kg"], x["vol_l"]), reverse=True)


def _render_items_html(items: list[dict], title: str, accent: str) -> str:
    if not items:
        return (
            f'<div class="item-empty"><span class="badge" style="background:{accent};">{escape(title)}</span>'
            f'<span class="item-muted">Sin ítems en esta sección</span></div>'
        )

    blocks = []
    for it in items:
        blocks.append(
            f"<div class='item-chip'>"
            f"<div class='item-title'>{escape(it['material'] or 'ITEM')}</div>"
            f"<div class='item-desc'>{escape(it['denominacion'] or it['material'] or '—')}</div>"
            f"<div class='item-meta'>{it['cantidad']} uds · {escape(it.get('uma', ''))}</div>"
            f"</div>"
        )

    return (
        f"<div class='item-section'>"
        f"<div class='section-head'><span class='badge' style='background:{accent};'>{escape(title)}</span></div>"
        f"<div class='item-grid'>{''.join(blocks)}</div></div>"
    )


def _build_route_map_html(stops_data: list[dict]) -> str:
    route_map = folium.Map(
        location=[config.DEPOT_LAT, config.DEPOT_LNG],
        zoom_start=11,
        tiles="CartoDB positron",
        control_scale=True,
    )

    folium.Marker(
        [config.DEPOT_LAT, config.DEPOT_LNG],
        tooltip=f"Salida - {config.DEPOT_NAME}",
        popup=f"<b>Depósito</b><br>{escape(config.DEPOT_NAME)}",
        icon=folium.Icon(color="red", icon="industry", prefix="fa"),
    ).add_to(route_map)

    coords = [[config.DEPOT_LAT, config.DEPOT_LNG]]
    for stop in stops_data:
        lat = float(stop.get("lat", 0) or 0)
        lng = float(stop.get("lng", 0) or 0)
        coords.append([lat, lng])
        order = int(stop.get("order", 0))
        cliente = escape(str(stop.get("cliente_nombre", f"Parada {order}")))
        poblacion = escape(str(stop.get("poblacion", "")))
        popup = (
            f"<b>{order}. {cliente}</b><br>"
            f"{poblacion}<br>"
            f"Entrega: {float(stop.get('entrega_l', 0)):.0f} L · Recogida: {float(stop.get('recogida_l', 0)):.0f} L"
        )
        folium.Marker(
            [lat, lng],
            tooltip=f"{order}. {stop.get('cliente_nombre', '')}",
            popup=popup,
            icon=folium.DivIcon(
                html=f"""
                <div style='width:30px;height:30px;border-radius:50%;background:#1d4ed8;color:white;display:flex;align-items:center;justify-content:center;border:2px solid white;box-shadow:0 2px 8px rgba(0,0,0,0.25);font-weight:700;font-size:12px;'>
                    {order}
                </div>"""
            ),
        ).add_to(route_map)

    if len(coords) > 1:
        folium.PolyLine(coords, color="#2563eb", weight=5, opacity=0.9).add_to(route_map)

    return route_map.get_root().render()


def _build_route_table_html(stops_data: list[dict]) -> str:
    rows = []
    for s in stops_data:
        materiales = s.get("materiales", []) or []
        entrega_items = _group_materials(materiales, retornable=False)
        recogida_items = _group_materials(materiales, retornable=True)
        entrega_html = _render_items_html(entrega_items, "Entregar", "#0ea5e9")
        recogida_html = _render_items_html(recogida_items, "Recoger", "#f59e0b")
        rows.append(
            f"<tr>"
            f"<td class='num'>{int(s.get('order', 0))}</td>"
            f"<td><b>{escape(str(s.get('cliente_nombre', '')))}</b><br><span class='muted'>{escape(str(s.get('poblacion', '')))}</span></td>"
            f"<td>{entrega_html}</td>"
            f"<td>{recogida_html}</td>"
            f"</tr>"
        )

    return f"""
    <div class='route-wrap'>
      <table class='route-table'>
        <thead>
          <tr><th>#</th><th>Cliente</th><th>Entregar</th><th>Recoger</th></tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </div>
    """


def _build_loading_overview_html(result: dict) -> str:
    total_entrega = float(result.get("entrega_total_l", 0) or 0)
    total_recogida = float(result.get("recogida_total_l", 0) or 0)
    peso = float(result.get("peso_kg", 0) or 0)
    stops_data = (result.get("snapshot_payload", {}) or {}).get("stops", [])

    top_productos = (result.get("snapshot_payload", {}) or {}).get("top_productos", [])[:8]
    cards = []
    for p in top_productos:
        cards.append(
            f"<div class='item-chip'>"
            f"<div class='item-title'>{escape(str(p.get('material', '')))}</div>"
            f"<div class='item-desc'>{escape(str(p.get('denominacion', '')))}</div>"
            f"<div class='item-meta'>{float(p.get('vol_l', 0) or 0):.0f} L · {float(p.get('peso_kg', 0) or 0):.0f} kg · {int(p.get('lineas', 0) or 0)} líneas</div>"
            f"</div>"
        )

    return f"""
    <div class='hero-card'>
      <div class='section-title'>📦 Resumen de carga</div>
      <div class='kpi-grid'>
        <div class='kpi'><div class='label'>Entrega</div><div class='value'>{total_entrega:.0f} L</div><div class='caption'>Volumen a entregar</div></div>
        <div class='kpi'><div class='label'>Recogida</div><div class='value'>{total_recogida:.0f} L</div><div class='caption'>Retornables / inversa</div></div>
        <div class='kpi'><div class='label'>Peso</div><div class='value'>{peso:.0f} kg</div><div class='caption'>Carga total</div></div>
        <div class='kpi'><div class='label'>Paradas</div><div class='value'>{len(stops_data)}</div><div class='caption'>Clientes en ruta</div></div>
      </div>
      <div style='margin-top:1rem;' class='section-title'>🔝 Productos principales</div>
      <div class='item-grid'>{''.join(cards) if cards else '<div class="item-muted">Sin top productos</div>'}</div>
    </div>
    """


def _slot_fill_color(fill_ratio: float) -> str:
    if fill_ratio <= 0:
        return "#cbd5e1"
    if fill_ratio < 0.25:
        return "#60a5fa"
    if fill_ratio < 0.5:
        return "#3b82f6"
    if fill_ratio < 0.75:
        return "#2563eb"
    if fill_ratio < 0.95:
        return "#22c55e"
    return "#16a34a"


def _slot_text_color(fill_ratio: float) -> str:
    return "#0f172a" if fill_ratio <= 0 else "white"


def _truncate(text: str, limit: int = 16) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)] + "…"


def _build_slot_assignment(plan: dict, truck_capacity_l: int, truck_code: str) -> dict:
    truck_spec = TRUCKS.get(truck_code, {})
    slot_count = max(1, int(truck_spec.get("palets", 1) or 1))
    slot_capacity = float(truck_capacity_l) / slot_count if slot_count else float(truck_capacity_l)

    items: list[dict] = []
    for zone in (plan.get("loading_zones", []) or []):
        for item in zone.get("items", []) or []:
            items.append(
                {
                    "name": str(item.get("name", "ITEM")),
                    "uma": _extract_uma(str(item.get("name", ""))),
                    "vol_l": float(item.get("vol_l", 0) or 0),
                    "peso_kg": float(item.get("peso_kg", 0) or 0),
                    "stops": item.get("stops", []) or [],
                    "retornable": bool(item.get("retornable", False)),
                }
            )

    items.sort(key=lambda x: (x["peso_kg"], x["vol_l"]), reverse=True)

    slots = [
        {
            "vol_l": 0.0,
            "peso_kg": 0.0,
            "items": [],
            "main": None,
        }
        for _ in range(slot_count)
    ]

    for item in items:
        slot_idx = min(range(slot_count), key=lambda idx: slots[idx]["vol_l"])
        slots[slot_idx]["vol_l"] += item["vol_l"]
        slots[slot_idx]["peso_kg"] += item["peso_kg"]
        slots[slot_idx]["items"].append(item)
        if slots[slot_idx]["main"] is None:
            slots[slot_idx]["main"] = item

    for slot in slots:
        slot["fill_ratio"] = min(1.0, slot["vol_l"] / slot_capacity) if slot_capacity else 0.0
        slot["main_label"] = _truncate(slot["main"]["name"], 16) if slot["main"] else "VACÍO"

    return {
        "slot_count": slot_count,
        "slot_capacity_l": slot_capacity,
        "slots": slots,
    }


# Colores por tipo de UMA/producto
UMA_COLORS = {
    "BRL": "#ef4444",  # barril
    "BID": "#f97316",
    "BOT": "#eab308",
    "CAJ": "#22c55e",  # caja
    "UN":  "#06b6d4",
    "EST": "#3b82f6",
    "TB":  "#8b5cf6",
    "UNK": "#94a3b8",
}


def _render_4_cage_container(slots: list[dict], cols_per_cage: int, truck_code: str) -> str:
    # Distribuir slots en 4 jaulas (izq->der)
    cages = [ [] for _ in range(4) ]
    for i, slot in enumerate(slots):
        cage_idx = min(3, i // cols_per_cage)
        cages[cage_idx].append((i, slot))

    cages_html = []
    for ci, cage in enumerate(cages):
        inner = []
        for idx, slot in cage:
            fill = slot.get("fill_ratio", 0.0)
            bg = _slot_fill_color(fill)
            fg = _slot_text_color(fill)
            main = escape(slot.get("main_label", "VACÍO"))
            vol = float(slot.get("vol_l", 0) or 0)
            items_n = len(slot.get("items", []) or [])
            color_dot = UMA_COLORS.get(slot.get("items", [{}])[0].get("uma","UNK") if slot.get("items") else "UNK", UMA_COLORS["UNK"])
            inner.append(f"<div class='seat-cell' style='background:{bg}; color:{fg};'><div class='slot-num'>{idx+1}</div><div class='slot-main'>{main}</div><div class='slot-meta'>{vol:.0f} L · {items_n} ítems</div></div>")
        cages_html.append(f"<div style='flex:1; padding:6px; display:flex; flex-direction:column; gap:8px;'>{''.join(inner) if inner else '<div class=\'seat-cell empty\'>VACÍO</div>'}</div>")

    return f"""
    <div style='display:flex; gap:8px; align-items:stretch;'>
        {''.join([f'<div style="flex:1; border:1px solid #e2e8f0; border-radius:12px; padding:8px; background:#fff"><div style="font-weight:800; color:#0f172a; margin-bottom:6px;">Jaula {i+1}</div>{cages_html[i]}</div>' for i in range(4)])}
    </div>
    """


def _build_loading_maps_html(plan: dict, truck_code: str, truck_capacity_l: int) -> str:
    truck_spec = TRUCKS.get(truck_code, {})
    palets = max(1, int(truck_spec.get("palets", 1) or 1))

    assignment = _build_slot_assignment(plan, truck_capacity_l, truck_code)
    slots = assignment["slots"]

    # Top: organizamos palets en filas/columnas y además mostramos el contenedor 4-jaulas
    cols_per_cage = max(1, math.ceil(palets / 4))
    container_html = _render_4_cage_container(slots, cols_per_cage, truck_code)

    # Lateral: fila única con todos los palets
    lateral_cells = []
    for idx, slot in enumerate(slots):
        fill = slot.get("fill_ratio", 0.0)
        bg = _slot_fill_color(fill)
        fg = _slot_text_color(fill)
        main = escape(slot.get("main_label", "VACÍO"))
        vol = float(slot.get("vol_l", 0) or 0)
        items_n = len(slot.get("items", []) or [])
        lateral_cells.append(f"<div class='seat-cell' style='background:{bg}; color:{fg}; min-width:78px; margin-right:6px;'><div class='slot-num'>{idx+1}</div><div class='slot-main'>{main}</div><div class='slot-meta'>{vol:.0f} L</div></div>")

    # Listas de carga/descarga: heurística simple LIFO
    # Descarga (orden de descarga): slots con items cuyo stop más alto primero
    slot_unload = []
    for idx, slot in enumerate(slots):
        max_stop = 0
        for it in slot.get('items', []) or []:
            if isinstance(it.get('stops', None), (list, tuple)) and it.get('stops'):
                max_stop = max(max_stop, max([int(s) for s in it.get('stops') if isinstance(s, int) or (isinstance(s,str) and s.isdigit())]))
        slot_unload.append((idx, max_stop))

    # Descargar: highest stop number first (furthest client unloaded first)
    slot_unload_sorted = sorted(slot_unload, key=lambda x: x[1], reverse=True)
    unload_html_items = []
    load_html_items = []
    for order_idx, (slot_idx, stop_val) in enumerate(slot_unload_sorted, 1):
        unload_html_items.append(f"<li><b>Bloque {slot_idx+1}</b> · parada {stop_val or '-'} </li>")

    # Carga: reverso (first loaded -> lowest stop first)
    for order_idx, (slot_idx, stop_val) in enumerate(reversed(slot_unload_sorted), 1):
        load_html_items.append(f"<li><b>Bloque {slot_idx+1}</b> · prioridad {order_idx}</li>")

    total_vol = sum(float(slot.get("vol_l", 0) or 0) for slot in slots)
    total_weight = sum(float(slot.get("peso_kg", 0) or 0) for slot in slots)
    utilization = (total_vol / truck_capacity_l * 100) if truck_capacity_l else 0.0

    full_html = f"""
    <div>
      <div class="hero-card">
        <div class="section-title">📦 Plan de carga - {truck_code}</div>
        <div class="loading-map-subtitle">Contenedor dividido en 4 jaulas (izquierda→derecha). Cada bloque numerado representa 1/{palets} parte del camión.</div>
        <div style='margin-bottom:12px;'>
            {container_html}
        </div>
        <div style='margin-top:8px;'>
            <div style='font-weight:800; margin-bottom:6px;'>Vista Lateral</div>
            <div style='display:flex; align-items:center;'>{''.join(lateral_cells)}</div>
        </div>
        <div style='margin-top:12px; display:flex; gap:12px;'>
            <div style='flex:1; background:#fff; border:1px solid #e2e8f0; padding:12px; border-radius:12px;'>
                <div style='font-weight:800; margin-bottom:8px;'>Orden de Carga</div>
                <ol>{''.join(load_html_items) or '<li>Sin asignaciones</li>'}</ol>
            </div>
            <div style='flex:1; background:#fff; border:1px solid #e2e8f0; padding:12px; border-radius:12px;'>
                <div style='font-weight:800; margin-bottom:8px;'>Orden de Descarga</div>
                <ol>{''.join(unload_html_items) or '<li>Sin asignaciones</li>'}</ol>
            </div>
        </div>
        <div style='margin-top:12px;' class='kpi-grid'>
            <div class='kpi'><div class='label'>Capacidad</div><div class='value'>{palets}P</div><div class='caption'>{truck_capacity_l} L</div></div>
            <div class='kpi'><div class='label'>Volumen asignado</div><div class='value'>{total_vol:.0f} L</div><div class='caption'>{utilization:.1f}% del camión</div></div>
            <div class='kpi'><div class='label'>Peso asignado</div><div class='value'>{total_weight:.0f} kg</div><div class='caption'>Distribuido en bloques</div></div>
            <div class='kpi'><div class='label'>Bloques</div><div class='value'>{len(slots)}</div><div class='caption'>Mapa se adapta al vehículo</div></div>
        </div>
      </div>
    </div>
    """

    return full_html


def _build_seat_map_html(title: str, subtitle: str, slots: list[dict], rows: int, cols: int, truck_code: str) -> str:
    cell_html = []
    total_slots = len(slots)
    for idx in range(rows * cols):
        slot_num = idx + 1
        slot = slots[idx] if idx < total_slots else None
        if slot is None:
            cell_html.append(
                f"""
                <div class="seat-cell empty">
                    <div class="slot-num">{slot_num}/-</div>
                    <div class="slot-pct">Sin posición</div>
                    <div class="slot-main">—</div>
                    <div class="slot-meta">{truck_code}</div>
                </div>
                """
            )
            continue

        fill_ratio = float(slot.get("fill_ratio", 0.0) or 0.0)
        bg = _slot_fill_color(fill_ratio)
        fg = _slot_text_color(fill_ratio)
        item_count = len(slot.get("items", []) or [])
        main_label = escape(str(slot.get("main_label", "VACÍO")))
        pct_label = f"{fill_ratio * 100:.0f}%"
        meta_label = f"{float(slot.get('vol_l', 0) or 0):.0f} L · {item_count} ítems"
        cell_html.append(
            f"""
            <div class="seat-cell" style="background:{bg}; color:{fg};">
                <div class="slot-num">{slot_num}/{total_slots}</div>
                <div class="slot-pct">{pct_label}</div>
                <div class="slot-main">{main_label}</div>
                <div class="slot-meta">{meta_label}</div>
            </div>
            """
        )

    return f"""
    <div class="loading-map-card">
        <div class="loading-map-title">{escape(title)}</div>
        <div class="loading-map-subtitle">{escape(subtitle)}</div>
        <div class="seat-grid" style="grid-template-columns: repeat({cols}, minmax(0, 1fr));">
            {''.join(cell_html)}
        </div>
        <div style="margin-top:0.7rem;">
            <span class="map-pill">{total_slots} bloques</span>
            <span class="map-pill">{truck_code}</span>
        </div>
    </div>
    """


def _build_loading_maps_html(plan: dict, truck_code: str, truck_capacity_l: int) -> str:
    truck_spec = TRUCKS.get(truck_code, {})
    palets = max(1, int(truck_spec.get("palets", 1) or 1))

    top_rows = 2 if palets >= 4 and palets % 2 == 0 else 1
    top_cols = max(1, math.ceil(palets / top_rows))

    assignment = _build_slot_assignment(plan, truck_capacity_l, truck_code)
    slots = assignment["slots"]

    top_html = _build_seat_map_html(
        title="Vista superior",
        subtitle="Plano tipo selector de asientos: cada bloque representa 1/x de la capacidad del camión.",
        slots=slots,
        rows=top_rows,
        cols=top_cols,
        truck_code=truck_code,
    )
    lateral_html = _build_seat_map_html(
        title="Vista lateral",
        subtitle="Vista de perfil con la misma partición de bloques, adaptada al tamaño del camión seleccionado.",
        slots=slots,
        rows=1,
        cols=palets,
        truck_code=truck_code,
    )

    total_vol = sum(float(slot.get("vol_l", 0) or 0) for slot in slots)
    total_weight = sum(float(slot.get("peso_kg", 0) or 0) for slot in slots)
    utilization = (total_vol / truck_capacity_l * 100) if truck_capacity_l else 0.0

    return f"""
    <div class="hero-card">
        <div class="section-title">📦 Plan de carga</div>
        <div class="loading-map-grid">
            {top_html}
            {lateral_html}
        </div>
        <div style="margin-top:0.85rem;" class="kpi-grid">
            <div class="kpi"><div class="label">Capacidad</div><div class="value">{palets}P</div><div class="caption">{truck_capacity_l} L</div></div>
            <div class="kpi"><div class="label">Volumen asignado</div><div class="value">{total_vol:.0f} L</div><div class="caption">{utilization:.1f}% del camión</div></div>
            <div class="kpi"><div class="label">Peso asignado</div><div class="value">{total_weight:.0f} kg</div><div class="caption">Distribuido en bloques</div></div>
            <div class="kpi"><div class="label">Bloques</div><div class="value">{len(slots)}</div><div class="caption">Mapa se adapta al vehículo</div></div>
        </div>
    </div>
    """


def _extract_uma(item_name: str) -> str:
    if "(" in item_name and ")" in item_name:
        return item_name.split("(")[-1].split(")")[0].strip().upper()
    return "UNK"


def _add_box_trace(
    fig: go.Figure,
    x0: float,
    x1: float,
    y0: float,
    y1: float,
    z0: float,
    z1: float,
    color: str,
    name: str,
    opacity: float = 0.9,
    showlegend: bool = False,
) -> None:
    x = [x0, x1, x1, x0, x0, x1, x1, x0]
    y = [y0, y0, y1, y1, y0, y0, y1, y1]
    z = [z0, z0, z0, z0, z1, z1, z1, z1]
    i = [0, 0, 4, 4, 0, 1, 2, 3, 0, 1, 2, 3]
    j = [1, 2, 5, 6, 4, 5, 6, 7, 3, 2, 7, 4]
    k = [2, 3, 6, 7, 1, 2, 3, 0, 4, 5, 6, 7]
    fig.add_trace(
        go.Mesh3d(
            x=x,
            y=y,
            z=z,
            i=i,
            j=j,
            k=k,
            color=color,
            opacity=opacity,
            flatshading=True,
            name=name,
            showlegend=showlegend,
            hovertemplate=f"{escape(name)}<extra></extra>",
        )
    )


def _build_loading_3d_figure(stops_data: list[dict], truck_capacity_l: int) -> go.Figure:
    plan = visualize_loading_plan(stops_data, truck_capacity_l)
    zones = plan.get("loading_zones", [])

    zone_layout = {
        "bay_no1": (0.0, 2.0, 6.0, 12.0),
        "bay_no2": (0.0, 2.0, 0.0, 6.0),
        "bay_su1": (2.0, 4.0, 6.0, 12.0),
        "bay_su2": (2.0, 4.0, 0.0, 6.0),
        "toldo_izq": (-0.7, 0.0, 0.0, 12.0),
        "toldo_der": (4.0, 4.7, 0.0, 12.0),
    }
    uma_colors = {
        "BRL": "#ef4444",
        "BID": "#f97316",
        "BOT": "#eab308",
        "CAJ": "#22c55e",
        "UN": "#06b6d4",
        "EST": "#3b82f6",
        "TB": "#8b5cf6",
        "UNK": "#94a3b8",
    }

    fig = go.Figure()

    for zone in zones:
        zid = zone.get("zone_id")
        if zid not in zone_layout:
            continue
        x0, x1, y0, y1 = zone_layout[zid]

        _add_box_trace(
            fig,
            x0,
            x1,
            y0,
            y1,
            0.0,
            0.05,
            color="#cbd5e1",
            name=f"Base {zone.get('zone_name', zid)}",
            opacity=0.4,
            showlegend=False,
        )

        items = zone.get("items", []) or []
        if not items:
            continue

        cols = 2 if zid.startswith("bay") else 1
        rows = max(1, (len(items) + cols - 1) // cols)
        cell_w = (x1 - x0) / cols
        cell_l = (y1 - y0) / rows

        for idx, item in enumerate(items):
            c = idx % cols
            r = idx // cols
            ix0 = x0 + c * cell_w + 0.08
            ix1 = x0 + (c + 1) * cell_w - 0.08
            iy0 = y0 + r * cell_l + 0.08
            iy1 = y0 + (r + 1) * cell_l - 0.08

            vol = float(item.get("vol_l", 0) or 0)
            zone_capacity = float(zone.get("capacity_l", 1) or 1)
            rel_h = max(0.15, min(2.8, (vol / zone_capacity) * 9.5))
            uma = _extract_uma(str(item.get("name", "")))
            color = uma_colors.get(uma, uma_colors["UNK"])
            name = f"{item.get('name', 'ITEM')} · {vol:.0f}L"

            _add_box_trace(
                fig,
                ix0,
                ix1,
                iy0,
                iy1,
                0.05,
                0.05 + rel_h,
                color=color,
                name=name,
                opacity=0.92,
                showlegend=False,
            )

    fig.update_layout(
        margin=dict(l=10, r=10, t=30, b=10),
        paper_bgcolor="#ffffff",
        scene=dict(
            xaxis=dict(title="Ancho del camión", showbackground=True, backgroundcolor="#f8fafc"),
            yaxis=dict(title="Largo del camión", showbackground=True, backgroundcolor="#f8fafc"),
            zaxis=dict(title="Altura de carga", showbackground=True, backgroundcolor="#f8fafc"),
            camera=dict(eye=dict(x=1.6, y=-1.9, z=1.1)),
            aspectmode="manual",
            aspectratio=dict(x=1.1, y=2.3, z=0.9),
        ),
        title="Distribución 3D de la carga por bahías y toldos",
    )
    return fig


st.sidebar.header("⚙️ Configuración")

try:
    df_canonical = _load_canonical_data()
    available_transports = sorted(df_canonical["transporte"].dropna().astype(str).unique().tolist())
except Exception as e:
    st.error(f"Error cargando datos: {e}")
    available_transports = []

transport_id = st.sidebar.text_input(
    "📍 ID Transporte",
    value="11561535" if available_transports and "11561535" in available_transports else (available_transports[0] if available_transports else ""),
    help="Ingresa el ID del transporte a optimizar",
)

truck_options = list(TRUCKS.keys())
truck_selected = st.sidebar.selectbox(
    "🚛 Tipo de Camión",
    options=truck_options,
    help="Capacidad de volumen y peso varían según el modelo",
)

mode = st.sidebar.radio(
    "🔄 Modo de Optimización",
    options=["Un solo camión", "Flota múltiple"],
    help="Single: optimiza una ruta. Fleet: distribuye entre múltiples vehículos",
)

fleet_size = 1
if mode == "Flota múltiple":
    fleet_size = st.sidebar.number_input(
        "📦 Número de vehículos",
        min_value=1,
        max_value=10,
        value=3,
        help="Máximo número de vehículos disponibles",
    )

enable_html = st.sidebar.checkbox("Exportar HTML (visualización interactiva)", value=True)
if st.sidebar.button("RESOLVER OPTIMIZACION", type="primary", use_container_width=True, key="solve_btn"):
    with st.container():
        progress_placeholder = st.empty()
        status_placeholder = st.empty()
        progress_placeholder.info("⏳ Iniciando optimización...")

        try:
            if not transport_id.isdigit():
                raise ValueError("El ID de transporte debe ser numérico")

            transport_int = int(transport_id)

            if mode == "Un solo camión":
                status_placeholder.info(f"Optimizando transporte {transport_id} con camión {truck_selected}...")
                result = run_for_transporte(
                    transporte_id=transport_int,
                    truck=truck_selected,
                    explain=True,
                    explain_lang="es",
                    loading_html="auto" if enable_html else None,
                )
                status_placeholder.success(f"✅ Optimización completada para camión {truck_selected}")
            else:
                status_placeholder.info(f"Optimizando transporte {transport_id} con flota de {fleet_size} camiones...")
                result = run_for_fleet(
                    transporte_id=transport_int,
                    n_vehicles=int(fleet_size),
                    truck=truck_selected,
                    explain=True,
                    explain_lang="es",
                    loading_html="auto" if enable_html else None,
                )
                vehicles_used = result.get("n_vehicles_used", fleet_size)
                status_placeholder.success(f"✅ Optimización completada: {vehicles_used} de {fleet_size} vehículos usados")

            snapshot_payload = result.get("snapshot_payload", {}) or {}
            stops_data = snapshot_payload.get("stops", []) or []
            metrics = snapshot_payload.get("metrics", {}) or {}

            tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
                "📊 Métricas",
                "🗺️ Ruta (Mapa)",
                "📦 Carga",
                "💡 Insights",
                "🧠 Explicaciones",
                "📄 Técnicalidades",
            ])

            with tab1:
                st.markdown('<div class="section-title">Métricas de Optimización</div>', unsafe_allow_html=True)
                total_vol = float(result.get("entrega_total_l", 0) or 0) + float(result.get("recogida_total_l", 0) or 0)
                truck_cap_l = TRUCKS[truck_selected]["vol_m3"] * 1000
                utilization = (total_vol / truck_cap_l * 100) if truck_cap_l else 0.0
                reduction_pct = abs(float(result.get("delta_dist_pct", 0) or 0))
                before_km = float(result.get("baseline_dist_m", 0) or 0) / 1000
                after_km = float(result.get("opt_dist_m", 0) or 0) / 1000

                st.markdown(
                    f"""
                    <div class='kpi-grid'>
                      <div class='kpi'><div class='label'>Paradas</div><div class='value'>{result.get('n_stops', 0)}</div><div class='caption'>Clientes atendidos</div></div>
                      <div class='kpi'><div class='label'>Volumen Total</div><div class='value'>{total_vol:.0f} L</div><div class='caption'>Entrega + recogida</div></div>
                      <div class='kpi'><div class='label'>Distancia</div><div class='value'>{after_km:.1f} km</div><div class='caption'>Antes {before_km:.1f} km</div></div>
                      <div class='kpi'><div class='label'>Ahorro de distancia</div><div class='value'>{reduction_pct:.1f}%</div><div class='caption'>Ruta más corta y eficiente</div></div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                st.markdown("<div style='margin-top:0.8rem;'></div>", unsafe_allow_html=True)
                summary_df = pd.DataFrame(
                    {
                        "Métrica": ["Paradas", "Volumen entrega", "Volumen recogida", "Peso total", "Distancia", "Tiempo", "Utilización"],
                        "Valor": [
                            result.get("n_stops", 0),
                            f"{float(result.get('entrega_total_l', 0) or 0):.1f} L",
                            f"{float(result.get('recogida_total_l', 0) or 0):.1f} L",
                            f"{float(result.get('peso_kg', 0) or 0):.1f} kg",
                            f"{after_km:.2f} km",
                            f"{float(result.get('opt_time_s', 0) or 0) / 3600:.1f} h",
                            f"{utilization:.1f}%",
                        ],
                    }
                )
                st.table(summary_df)

            with tab2:
                st.markdown('<div class="section-title">Ruta optimizada completa</div>', unsafe_allow_html=True)
                st.caption("Mapa interactivo estilo Google Maps + tabla completa con entregas y recogidas concretas.")

                if stops_data:
                    st.components.v1.html(_build_route_map_html(stops_data), height=720, scrolling=False)
                    with st.expander("Ver ruta completa en detalle", expanded=True):
                        st.markdown(_build_route_table_html(stops_data), unsafe_allow_html=True)
                else:
                    st.info("ℹ️ No hay datos de ruta disponibles")

            with tab3:
                st.markdown('<div class="section-title">Plan de Carga</div>', unsafe_allow_html=True)
                if stops_data:
                    truck_capacity_l = int(TRUCKS[truck_selected]["vol_m3"] * 1000)
                    loading_plan = visualize_loading_plan(stops_data, truck_capacity_l)
                    import streamlit.components.v1 as components
                    html_blob = _build_loading_maps_html(loading_plan, truck_selected, truck_capacity_l)
                    components.html(html_blob, height=720, scrolling=True)
                    st.caption("Mapa 2D tipo selector de asientos: vista superior y lateral, ambos sincronizados con el camión seleccionado.")
                else:
                    st.info("ℹ️ No hay datos de carga para visualizar")

                st.markdown(_build_loading_overview_html(result), unsafe_allow_html=True)

            with tab4:
                st.markdown('<div class="section-title">Análisis e Insights</div>', unsafe_allow_html=True)
                total_entrega = float(result.get("entrega_total_l", 0) or 0)
                total_recogida = float(result.get("recogida_total_l", 0) or 0)
                peso_total = float(result.get("peso_kg", 0) or 0)

                st.markdown(
                    f"""
                    <div class='kpi-grid'>
                      <div class='kpi'><div class='label'>Reducción distancia</div><div class='value'>{abs(float(result.get('delta_dist_pct', 0) or 0)):.1f}%</div><div class='caption'>Ahorro vs baseline</div></div>
                      <div class='kpi'><div class='label'>Reducción tiempo</div><div class='value'>{abs(float(result.get('delta_time_pct', 0) or 0)):.1f}%</div><div class='caption'>Ahorro temporal</div></div>
                      <div class='kpi'><div class='label'>Entrega</div><div class='value'>{total_entrega:.0f} L</div><div class='caption'>Producto a repartir</div></div>
                      <div class='kpi'><div class='label'>Recogida</div><div class='value'>{total_recogida:.0f} L</div><div class='caption'>Retornables a recoger</div></div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                st.divider()
                st.subheader("📌 Qué se entrega y qué se recoge")
                all_materials = [m for s in stops_data for m in (s.get("materiales", []) or [])]
                col_a, col_b = st.columns(2)

                with col_a:
                    entrega_agg = _group_materials(all_materials, retornable=False)
                    if entrega_agg:
                        st.dataframe(
                            pd.DataFrame(
                                [
                                    {
                                        "Material": x["material"],
                                        "Descripción": x["denominacion"],
                                        "Unidades": x["cantidad"],
                                        "Peso": f"{x['peso_kg']:.0f} kg",
                                    }
                                    for x in entrega_agg
                                ]
                            ),
                            use_container_width=True,
                            hide_index=True,
                        )
                    else:
                        st.info("Sin entregas registradas")

                with col_b:
                    recogida_agg = _group_materials(all_materials, retornable=True)
                    if recogida_agg:
                        st.dataframe(
                            pd.DataFrame(
                                [
                                    {
                                        "Material": x["material"],
                                        "Descripción": x["denominacion"],
                                        "Unidades": x["cantidad"],
                                        "Peso": f"{x['peso_kg']:.0f} kg",
                                    }
                                    for x in recogida_agg
                                ]
                            ),
                            use_container_width=True,
                            hide_index=True,
                        )
                    else:
                        st.info("Sin recogidas registradas")

                st.divider()
                st.subheader("💡 Recomendaciones operacionales")
                recommendations = [
                    "Consolidar micro-paradas: agrupar clientes pequeños cercanos.",
                    "Equilibrar carga entre bahías: usar el 4-bay layout para estabilidad.",
                    "Usar toldos laterales para retornables: evita bloqueos de descarga.",
                    "Reorganizar almacén por frecuencia: productos más usados más cerca del muelle.",
                ]
                for i, rec in enumerate(recommendations, 1):
                    st.write(f"**{i}. {rec}**")

                st.caption(f"Peso total calculado: {peso_total:.0f} kg")

            with tab5:
                st.markdown('<div class="section-title">Explainability de la solución</div>', unsafe_allow_html=True)
                explanations = result.get("explanations", {}) or {}

                if explanations:
                    if isinstance(explanations, dict):
                        for key, value in explanations.items():
                            title = str(key).replace("_", " ").title()
                            st.markdown(f"### {title}")
                            if isinstance(value, str):
                                st.write(value)
                            else:
                                st.json(value)
                    else:
                        st.write(explanations)
                else:
                    st.info("No se han generado explicaciones para esta ejecución.")

            with tab6:
                st.markdown('<div class="section-title">Detalles Técnicos</div>', unsafe_allow_html=True)
                col1, col2 = st.columns(2)

                with col1:
                    st.subheader("Parámetros de Entrada")
                    st.json(
                        {
                            "Transport ID": transport_id,
                            "Tipo Camión": truck_selected,
                            "Modo": mode,
                            "Estado": result.get("status", "Desconocido"),
                        }
                    )

                with col2:
                    st.subheader("Capacidades del Camión")
                    truck_specs = TRUCKS.get(truck_selected, {})
                    st.json(
                        {
                            "Volumen Máximo": f"{truck_specs.get('vol_m3', 0) * 1000:.0f}L",
                            "Peso Máximo": f"{truck_specs.get('peso_max_kg', 0):.0f}kg",
                        }
                    )

                st.divider()
                st.subheader("Resultado JSON Completo")
                st.json(result)

            progress_placeholder.empty()

        except Exception as e:
            st.error(f"❌ Error durante la optimización: {str(e)}")
            import traceback

            st.code(traceback.format_exc(), language="python")


st.divider()
st.markdown(
    """
---
**Damm Smart Truck** - InterHack 2026 Challenge
- 🚛 Optimización de rutas VRP/CVRP con OR-Tools
- 📊 Distribución inteligente de cargas multi-bahía
- 🔄 Logística inversa (retornables)
- 💡 Explainability automática con LLM
- 🔧 Multiidioma (ES/EN)
"""
)

with st.expander("ℹ️ Cómo ejecutar este dashboard"):
    st.code(
        """
# Desde la raíz del proyecto
streamlit run app/dashboard.py

# O con python explícito desde venv
python -m streamlit run app/dashboard.py
        """,
        language="bash",
    )