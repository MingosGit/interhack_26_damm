"""
Dashboard interactivo de Streamlit para Damm Smart Truck.
Muestra mapa de ruta, carga, insights y detalles técnicos.
"""
from html import escape
import math
from pathlib import Path
import sys
# Añadir src al path
sys.path.insert(0, str(Path(__file__).parent.parent))
import streamlit as st
from src.loading_visualization import TruckVisualizer
import folium
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import requests


from src.routing import get_route_geometry
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
    /* =========================================
       1. ESTILOS ANTIGUOS RECUPERADOS (Métricas, Tablas, Tarjetas)
       ========================================= */
    .main-header {{
        font-size: 2.4rem;
        font-weight: 800;
        color: #0f172a;
        letter-spacing: -0.02em;
        margin-bottom: 0.25rem;
    }}
    .sub-header {{
        color: #475569;
        margin-top: 0;
        margin-bottom: 1rem;
    }}
    .hero-card {{
        background: linear-gradient(135deg, #eff6ff 0%, #f8fafc 100%);
        border: 1px solid #dbeafe;
        border-radius: 18px;
        padding: 1rem 1.1rem;
        box-shadow: 0 10px 25px rgba(15, 23, 42, 0.05);
        margin-bottom: 0.75rem;
    }}
    .section-title {{
        font-size: 1.15rem;
        font-weight: 700;
        color: #0f172a;
        margin-top: 0.4rem;
        margin-bottom: 0.4rem;
    }}
    .muted {{ color: #64748b; }}
    .badge {{
        display: inline-block;
        color: white;
        padding: 0.2rem 0.55rem;
        border-radius: 999px;
        font-size: 0.72rem;
        font-weight: 700;
        margin-right: 0.35rem;
    }}
    .route-wrap {{ width: 100%; }}
    .route-table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 0.92rem;
        table-layout: fixed;
    }}
    .route-table th, .route-table td {{
        border: 1px solid #e2e8f0;
        vertical-align: top;
        padding: 0.7rem;
    }}
    .route-table th {{
        background: #0f172a;
        color: white;
        text-align: left;
    }}
    .route-table tr:nth-child(even) {{ background: #f8fafc; }}
    .route-table td.num {{
        text-align: center;
        font-weight: 800;
        width: 48px;
    }}
    .item-section {{ margin-bottom: 0.2rem; }}
    .section-head {{ margin-bottom: 0.45rem; }}
    .item-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
        gap: 0.45rem;
    }}
    .item-chip {{
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 14px;
        padding: 0.55rem 0.65rem;
        box-shadow: 0 4px 10px rgba(15, 23, 42, 0.04);
    }}
    .item-title {{
        font-weight: 800;
        color: #0f172a;
        font-size: 0.88rem;
    }}
    .item-desc {{
        color: #334155;
        font-size: 0.8rem;
        margin-top: 0.15rem;
    }}
    .item-meta {{
        color: #64748b;
        font-size: 0.78rem;
        margin-top: 0.2rem;
    }}
    .item-empty {{
        display: flex;
        flex-direction: column;
        gap: 0.25rem;
    }}
    .item-muted {{ color: #94a3b8; font-size: 0.82rem; }}
    .map-card {{
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 18px;
        padding: 0.4rem;
        box-shadow: 0 10px 25px rgba(15, 23, 42, 0.05);
    }}
    .kpi-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
        gap: 0.8rem;
    }}
    .kpi {{
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 16px;
        padding: 0.9rem;
        box-shadow: 0 8px 18px rgba(15, 23, 42, 0.05);
    }}
    .kpi .label {{ color: #64748b; font-size: 0.78rem; font-weight: 700; }}
    .kpi .value {{ font-size: 1.55rem; font-weight: 800; color: #0f172a; margin-top: 0.15rem; }}
    .kpi .caption {{ color: #475569; font-size: 0.8rem; margin-top: 0.2rem; }}
    .loading-map-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
        gap: 0.9rem;
        margin-top: 0.9rem;
    }}
    .loading-map-card {{
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 18px;
        padding: 0.9rem;
        box-shadow: 0 10px 25px rgba(15, 23, 42, 0.05);
    }}
    .loading-map-title {{
        font-size: 1rem;
        font-weight: 800;
        color: #0f172a;
        margin-bottom: 0.15rem;
    }}
    .loading-map-subtitle {{
        color: #64748b;
        font-size: 0.78rem;
        margin-bottom: 0.75rem;
    }}
    .seat-grid {{
        display: grid;
        gap: 0.5rem;
    }}
    .seat-cell {{
        min-height: 78px;
        border-radius: 14px;
        padding: 0.55rem 0.6rem;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        border: 1px solid rgba(255, 255, 255, 0.68);
        box-shadow: inset 0 -18px 24px rgba(255, 255, 255, 0.11);
        color: white;
    }}
    .seat-cell.empty {{
        background: linear-gradient(180deg, #e2e8f0 0%, #cbd5e1 100%);
        color: #334155;
        border-color: #e2e8f0;
        box-shadow: inset 0 -16px 20px rgba(255, 255, 255, 0.25);
    }}
    .seat-cell .slot-num {{
        font-size: 0.92rem;
        font-weight: 800;
        line-height: 1;
    }}
    .seat-cell .slot-pct {{
        font-size: 0.8rem;
        font-weight: 700;
        opacity: 0.95;
    }}
    .seat-cell .slot-main {{
        font-size: 0.72rem;
        line-height: 1.15;
        opacity: 0.95;
    }}
    .seat-cell .slot-meta {{
        font-size: 0.68rem;
        opacity: 0.8;
    }}
    .map-pill {{
        display: inline-block;
        background: #dbeafe;
        color: #1e3a8a;
        border-radius: 999px;
        padding: 0.16rem 0.55rem;
        font-size: 0.72rem;
        font-weight: 800;
        margin-right: 0.35rem;
    }}

    /* =========================================
       2. TUS ESTILOS NUEVOS DAMM (Fondos, logo girando, etc.)
       ========================================= */
    .stApp, [data-testid="stSidebar"], [data-testid="stHeader"], [data-testid="stToolbar"] {{
        background-color: #ffffff !important;
    }}

    [data-testid="stSidebarHeader"] {{
        padding-top: 0rem !important;
        margin-top: -2.8rem !important;
    }}
    
    [data-testid="stSidebarNav"] {{
        padding-top: 0rem !important;
    }}

    div.stButton > button[kind="primary"] {{
        background-color: #E20613 !important;
        color: #FFFFFF !important;
        border-color: #E20613 !important;
    }}
    
    div.stButton > button[kind="primary"]:hover {{
        background-color: #B8050F !important; 
        border-color: #B8050F !important;
    }}

    .block-container {{
        padding-top: 2rem !important;
    }}

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

    [data-testid="stStatusWidget"] > div {{
        display: none !important;
    }}
    
    [data-testid="stStatusWidget"]::before {{
        content: "";
        display: block;
        width: 35px;
        height: 35px;
        background-image: url("data:image/jpeg;base64,{logo_b64}");
        background-size: contain;
        background-repeat: no-repeat;
        background-position: center;
        animation: spin 1.2s linear infinite; 
        border-radius: 8px; 
    }}

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

def get_osrm_route_geometry(coords: list[list[float]]) -> list[list[float]] | None:
    """Obtiene la ruta real por carreteras usando la API pública de OSRM."""
    try:
        coords_str = ";".join([f"{lng},{lat}" for lat, lng in coords])
        # Añadimos continue_straight=true para evitar U-turns raros en las paradas
        url = f"https://router.project-osrm.org/route/v1/driving/{coords_str}?overview=full&geometries=geojson&continue_straight=true"
        
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if data.get("code") == "Ok":
                geom = data["routes"][0]["geometry"]["coordinates"]
                return [[lat, lng] for lng, lat in geom]
    except Exception as e:
        print(f"Error obteniendo geometría de ruta: {e}")
        
    return None

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

    # Coordenadas exactas para calcular la ruta por carretera
    routing_coords = [[config.DEPOT_LAT, config.DEPOT_LNG]]
    
    # Diccionario para rastrear coordenadas duplicadas
    seen_coords = {}

    for stop in stops_data:
        lat_real = float(stop.get("lat", 0) or 0)
        lng_real = float(stop.get("lng", 0) or 0)
        
        # Ignorar coordenadas (0,0) en caso de que un geocoding fallara por completo
        if lat_real == 0 and lng_real == 0:
            continue
            
        routing_coords.append([lat_real, lng_real])
        
        # --- SOLUCIÓN A PARADAS SOLAPADAS ---
        coord_key = (round(lat_real, 4), round(lng_real, 4))
        offset_count = seen_coords.get(coord_key, 0)
        seen_coords[coord_key] = offset_count + 1
        
        # Si ya hay una parada ahí, le sumamos un micro-desplazamiento visual (aprox 15 metros)
        lat_display = lat_real + (offset_count * 0.00015)
        lng_display = lng_real + (offset_count * 0.00015)

        order = int(stop.get("order", 0))
        cliente = escape(str(stop.get("cliente_nombre", f"Parada {order}")))
        poblacion = escape(str(stop.get("poblacion", "")))
        popup = (
            f"<b>{order}. {cliente}</b><br>"
            f"{poblacion}<br>"
            f"Entrega: {float(stop.get('entrega_l', 0)):.0f} L · Recogida: {float(stop.get('recogida_l', 0)):.0f} L"
        )
        
        # Pintamos de MORADO las paradas desplazadas para que sepas que en realidad comparten edificio con la anterior
        bg_color = "#1d4ed8" if offset_count == 0 else "#9333ea" 
        
        folium.Marker(
            [lat_display, lng_display],
            tooltip=f"{order}. {stop.get('cliente_nombre', '')}",
            popup=popup,
            icon=folium.DivIcon(
                html=f"""
                <div style='width:30px;height:30px;border-radius:50%;background:{bg_color};color:white;display:flex;align-items:center;justify-content:center;border:2px solid white;box-shadow:0 2px 8px rgba(0,0,0,0.25);font-weight:700;font-size:12px;'>
                    {order}
                </div>"""
            ),
        ).add_to(route_map)

    if len(routing_coords) > 1:
        route_geom = get_osrm_route_geometry(routing_coords)
        
        if route_geom:
            folium.PolyLine(
                route_geom, 
                color="#2563eb", 
                weight=5, 
                opacity=0.8,
                tooltip="Ruta por carretera"
            ).add_to(route_map)
        else:
            folium.PolyLine(routing_coords, color="#ef4444", weight=5, opacity=0.8, dash_array="10").add_to(route_map)

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

def _get_item_style_and_icon(uma: str) -> tuple[str, str]:
    """Asigna estilo visual y emoji según el tipo de unidad de carga."""
    uma = str(uma).upper()
    if uma in ["BRL", "BID"]: 
        return "type-barril", "🛢️" # Icono de barril
    if uma in ["CAJ", "EST", "PQ"]: 
        return "type-caja", "📦"   # Icono de caja
    if uma in ["BOT", "PAK", "TB", "UN"]: 
        return "type-botella", "🥤" # Icono de bebidas
    return "type-otro", "🧊"

def _build_loading_maps_html(plan: dict, truck_code: str, truck_capacity_l: int) -> str:
    truck_spec = config.TRUCKS.get(truck_code, {})
    palets = max(1, int(truck_spec.get("palets", 1) or 1))

    assignment = _build_slot_assignment(plan, truck_capacity_l, truck_code)
    slots = assignment["slots"]
    slot_cap = assignment["slot_capacity_l"]

    # Lógica de Jaulas: Dividimos el espacio en 4 compartimentos visuales
    num_jaulas = 4 if palets >= 4 else palets
    jaulas = [[] for _ in range(num_jaulas)]
    for i, slot in enumerate(slots):
        idx_j = min(num_jaulas - 1, i * num_jaulas // len(slots))
        jaulas[idx_j].append(slot)

    # --- VISTA LATERAL (Perfil del camión con Cabina y Ruedas) ---
    lateral_html = ""
    for j_idx, contenido_jaula in enumerate(jaulas):
        columnas_html = ""
        for slot in contenido_jaula:
            items_html = ""
            for item in slot["items"]:
                uma = _extract_uma(item.get("name", ""))
                estilo, icono = _get_item_style_and_icon(uma)
                vol = float(item.get("vol_l", 0) or 0)
                # Altura proporcional al volumen (mínimo 15% para que sea clicable)
                h_pct = min(100, max(15, (vol / slot_cap) * 100)) if slot_cap else 15
                
                items_html += f'''
                <div class="item-cargo {estilo}" style="height:{h_pct}%;">
                    <span style="font-size:16px;">{icono}</span>
                    <span class="cargo-txt">{vol:.0f}L</span>
                </div>'''
            
            columnas_html += f'<div class="slot-column">{items_html or "<div class=\'empty-txt\'>VACÍO</div>"}</div>'
        lateral_html += f'<div class="jaula-compartimento">{columnas_html}</div>'

    # --- VISTA SUPERIOR (Top-down) ---
    superior_html = ""
    for j_idx, contenido_jaula in enumerate(jaulas):
        bloques_top = ""
        for slot in contenido_jaula:
            fill = slot.get("fill_ratio", 0) * 100
            uma_top = _extract_uma(slot.get("main", {}).get("uma", "")) if slot.get("main") else ""
            estilo_top, icono_top = _get_item_style_and_icon(uma_top)
            
            bloques_top += f'''
            <div class="top-block {estilo_top if fill > 0 else 'empty-block'}">
                <div style="font-size:22px;">{icono_top if fill > 0 else ''}</div>
                <div class="top-txt"><b>{escape(slot.get("main_label", "VACÍO"))}</b></div>
                <div class="top-sub">{slot.get("vol_l", 0):.0f}L ({fill:.0f}%)</div>
            </div>'''
        superior_html += f'<div class="jaula-top">{bloques_top}</div>'

    # --- CSS Y ENSAMBLAJE ---
    return f"""
    <style>
        .truck-container {{ display: flex; align-items: flex-end; padding: 20px 0; margin-bottom: 40px; }}
        .cabina {{ width: 80px; height: 160px; background: #E20613; border: 4px solid #1e293b; border-radius: 20px 5px 5px 5px; position: relative; }}
        .ventana {{ background: #cbd5e1; height: 60px; margin: 15px 10px; border-radius: 10px 5px 0 0; border: 2px solid #1e293b; }}
        
        .caja-carga {{ flex: 1; height: 250px; background: #f8fafc; border: 4px solid #1e293b; border-left: none; display: flex; position: relative; }}
        .jaula-compartimento {{ flex: 1; border-right: 3px dashed #94a3b8; display: flex; padding: 10px; gap: 8px; }}
        .jaula-compartimento:last-child {{ border-right: none; }}
        
        .slot-column {{ flex: 1; display: flex; flex-direction: column-reverse; background: rgba(226, 232, 240, 0.5); border-radius: 8px; padding: 5px; gap: 4px; }}
        .item-cargo {{ display: flex; align-items: center; justify-content: center; color: white; border-radius: 6px; border: 1px solid rgba(0,0,0,0.2); box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .type-caja {{ background: linear-gradient(135deg, #16a34a, #15803d); }}
        .type-barril {{ background: linear-gradient(135deg, #dc2626, #991b1b); border-radius: 10px; }}
        .type-botella {{ background: linear-gradient(135deg, #2563eb, #1d4ed8); }}
        .cargo-txt {{ font-size: 10px; font-weight: bold; margin-left: 2px; }}
        
        .ruedas-box {{ position: absolute; bottom: -25px; width: 100%; display: flex; justify-content: space-around; }}
        .rueda {{ width: 45px; height: 45px; background: #334155; border: 5px solid #0f172a; border-radius: 50%; }}
        
        .top-view {{ display: flex; background: #94a3b8; padding: 15px; border-radius: 12px; gap: 10px; border: 4px solid #334155; }}
        .jaula-top {{ flex: 1; display: flex; gap: 8px; background: white; padding: 8px; border-radius: 8px; border: 2px dashed #cbd5e1; }}
        .top-block {{ flex: 1; height: 100px; display: flex; flex-direction: column; align-items: center; justify-content: center; border-radius: 6px; color: white; }}
        .empty-block {{ background: #f1f5f9; border: 2px dashed #cbd5e1; color: #94a3b8; }}
        .top-txt {{ font-size: 11px; margin-top: 5px; text-align: center; }}
        .top-sub {{ font-size: 10px; opacity: 0.8; }}
    </style>

    <div class="hero-card">
        <h3 style="color:#0f172a; margin-bottom:5px;">🚛 Plan de Carga Visual: {truck_code}</h3>
        <p style="color:#64748b; font-size:0.9em; margin-bottom:25px;">Distribución por jaulas y apilamiento real de productos.</p>
        
        <h4 style="color:#475569;">Vista Lateral (Perfil)</h4>
        <div class="truck-container">
            <div class="cabina"><div class="ventana"></div></div>
            <div class="caja-carga">
                {lateral_html}
                <div class="ruedas-box">
                    <div class="rueda"></div><div class="rueda"></div><div class="rueda"></div>
                </div>
            </div>
        </div>

        <h4 style="color:#475569; margin-top:20px;">Vista Superior (Distribución de Planta)</h4>
        <div class="top-view">
            <div style="width:70px; background:#E20613; border-radius:8px; border:3px solid #334155;"></div>
            <div style="display:flex; flex:1; gap:10px;">{superior_html}</div>
        </div>

        <div style="margin-top:30px;" class="kpi-grid">
            <div class="kpi"><div class="label">Capacidad</div><div class="value">{palets}P</div><div class="caption">{truck_capacity_l}L</div></div>
            <div class="kpi"><div class="label">Ocupación</div><div class="value">{((sum(s['vol_l'] for s in slots)/truck_capacity_l)*100):.1f}%</div><div class="caption">Carga dinámica</div></div>
            <div class="kpi"><div class="label">Peso</div><div class="value">{sum(s['peso_kg'] for s in slots):.0f}kg</div><div class="caption">Seguridad vial</div></div>
        </div>
    </div>
    """

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