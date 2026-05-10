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
from src.insights import analyze_route, get_top_materiales_by_frequency
from src.warehouse import recommend_warehouse_layout, picking_path_for_route
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


VEHICLE_COLORS = [
    "#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c",
    "#0891b2", "#db2777", "#65a30d", "#7c3aed", "#0d9488",
]


def _vehicle_color(vehicle_index: int) -> str:
    return VEHICLE_COLORS[(int(vehicle_index) - 1) % len(VEHICLE_COLORS)]


def _build_multi_route_map_html(routes_data: list[dict]) -> str:
    """Mapa folium con varias rutas (una por vehículo) coloreadas y conmutables."""
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

    seen_coords: dict[tuple[float, float], int] = {}

    for route in routes_data:
        vehicle_idx = int(route.get("vehicle_index", 1))
        color = _vehicle_color(vehicle_idx)
        stops = route.get("stops", []) or []
        if not stops:
            continue

        layer = folium.FeatureGroup(
            name=f"Vehículo {vehicle_idx} ({len(stops)} paradas)",
            show=True,
        )

        routing_coords = [[config.DEPOT_LAT, config.DEPOT_LNG]]

        for stop in stops:
            lat_real = float(stop.get("lat", 0) or 0)
            lng_real = float(stop.get("lng", 0) or 0)
            if lat_real == 0 and lng_real == 0:
                continue

            routing_coords.append([lat_real, lng_real])

            coord_key = (round(lat_real, 4), round(lng_real, 4))
            offset_count = seen_coords.get(coord_key, 0)
            seen_coords[coord_key] = offset_count + 1
            lat_display = lat_real + (offset_count * 0.00015)
            lng_display = lng_real + (offset_count * 0.00015)

            order = int(stop.get("order", 0))
            cliente = escape(str(stop.get("cliente_nombre", f"Parada {order}")))
            poblacion = escape(str(stop.get("poblacion", "")))
            popup = (
                f"<b>V{vehicle_idx} · {order}. {cliente}</b><br>"
                f"{poblacion}<br>"
                f"Entrega: {float(stop.get('entrega_l', 0)):.0f} L · Recogida: {float(stop.get('recogida_l', 0)):.0f} L"
            )

            folium.Marker(
                [lat_display, lng_display],
                tooltip=f"V{vehicle_idx}-{order}. {stop.get('cliente_nombre', '')}",
                popup=popup,
                icon=folium.DivIcon(
                    html=(
                        f"<div style='width:32px;height:32px;border-radius:50%;background:{color};"
                        "color:white;display:flex;align-items:center;justify-content:center;"
                        "border:2px solid white;box-shadow:0 2px 8px rgba(0,0,0,0.25);"
                        f"font-weight:700;font-size:11px;line-height:1;'>"
                        f"<span style='display:flex;flex-direction:column;align-items:center;'>"
                        f"<span style='font-size:8px;opacity:0.85;'>V{vehicle_idx}</span>"
                        f"<span>{order}</span></span></div>"
                    )
                ),
            ).add_to(layer)

        if len(routing_coords) > 1:
            route_geom = get_osrm_route_geometry(routing_coords)
            if route_geom:
                folium.PolyLine(
                    route_geom, color=color, weight=4, opacity=0.78,
                    tooltip=f"Vehículo {vehicle_idx}",
                ).add_to(layer)
            else:
                folium.PolyLine(
                    routing_coords, color=color, weight=4, opacity=0.78,
                    dash_array="10", tooltip=f"Vehículo {vehicle_idx} (línea recta)",
                ).add_to(layer)

        layer.add_to(route_map)

    folium.LayerControl(collapsed=False).add_to(route_map)
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
    payload = result.get("snapshot_payload", {}) or {}
    stops_data = payload.get("stops") or []
    if not stops_data:
        # Fleet mode: aggregate stops from all vehicle routes
        stops_data = [s for r in (payload.get("routes") or []) for s in (r.get("stops") or [])]

    top_productos = list(payload.get("top_productos") or [])
    if not top_productos:
        # Fleet mode: aggregate top_productos across vehicles
        bag: dict[str, dict] = {}
        for r in (payload.get("routes") or []):
            for p in (r.get("top_productos") or []):
                key = str(p.get("material", ""))
                entry = bag.setdefault(key, {
                    "material": key,
                    "denominacion": p.get("denominacion", ""),
                    "vol_l": 0.0,
                    "peso_kg": 0.0,
                    "lineas": 0,
                })
                entry["vol_l"] += float(p.get("vol_l", 0) or 0)
                entry["peso_kg"] += float(p.get("peso_kg", 0) or 0)
                entry["lineas"] += int(p.get("lineas", 0) or 0)
        top_productos = sorted(bag.values(), key=lambda x: x["vol_l"], reverse=True)
    top_productos = top_productos[:8]
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


def _truncate(text: str, limit: int = 16) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)] + "…"


def _zone_fill_color(ratio: float) -> str:
    """Color escalonado por nivel de llenado de zona."""
    if ratio <= 0:
        return "#94a3b8"
    if ratio < 0.4:
        return "#22c55e"
    if ratio < 0.7:
        return "#3b82f6"
    if ratio < 0.9:
        return "#0ea5e9"
    if ratio <= 1.0:
        return "#16a34a"
    return "#ef4444"


def _build_zone_card_html(zone: dict, label: str) -> str:
    cap = float(zone.get("capacity_l", 1) or 1)
    vol = float(zone.get("current_volume_l", 0) or 0)
    ratio = vol / cap if cap else 0.0
    pct = ratio * 100
    pct_clamped = min(100, pct)
    color = _zone_fill_color(ratio)
    items = zone.get("items", []) or []
    n_items = len(items)
    weight_kg = sum(float(it.get("peso_kg", 0) or 0) for it in items)

    top_items = sorted(items, key=lambda x: float(x.get("vol_l", 0) or 0), reverse=True)
    top_name = top_items[0].get("name", "—") if top_items else "Vacío"
    top_name_short = _truncate(top_name, 24)

    return f"""
    <div class="zone-card" style="--accent:{color};">
      <div class="zone-card-head">
        <span class="zone-card-label">{escape(label)}</span>
        <span class="zone-card-pct" style="color:{color};">{pct:.0f}%</span>
      </div>
      <div class="zone-card-bar"><div class="zone-card-fill" style="width:{pct_clamped:.1f}%; background:{color};"></div></div>
      <div class="zone-card-meta">
        <span><b>{vol:.0f} L</b> / {cap:.0f} L</span>
        <span>{weight_kg:.0f} kg</span>
      </div>
      <div class="zone-card-top" title="{escape(top_name)}">{escape(top_name_short)}</div>
      <div class="zone-card-foot">{n_items} línea{'s' if n_items != 1 else ''}</div>
    </div>
    """


_ROW_FULL_LABEL = {"n": "Norte", "s": "Sur", "m": "Centro"}


def _build_truck_schematic_html(plan: dict) -> str:
    zones = {z.get("zone_id"): z for z in plan.get("loading_zones", []) or []}
    layout = plan.get("truck_layout", {}) or {}
    n_cols = int(layout.get("n_cols", 2))
    row_codes = layout.get("row_codes") or ["n", "s"]
    has_toldos = bool(layout.get("has_toldos", True))
    truck_code = plan.get("truck_code", "")

    # Render rows × cols of bay cards
    rows_html = []
    for ri, rcode in enumerate(row_codes):
        cards = []
        for c in range(1, n_cols + 1):
            zid = f"bay_{rcode}{c}"
            zone = zones.get(zid)
            if zone is None:
                continue
            row_label = _ROW_FULL_LABEL.get(rcode, rcode.upper())
            label = f"Bahía {_ROW_FULL_LABEL.get(rcode, '?')[0]}-{c}" if len(row_codes) > 1 \
                else f"Palet {c}"
            cards.append(_build_zone_card_html(zone, label))

        row_label_short = rcode.upper()
        rows_html.append(
            f"<div class='truck-row'>"
            f"  <div class='truck-row-label'>{row_label_short}</div>"
            f"  <div class='truck-row-content' style='grid-template-columns: repeat({n_cols}, 1fr);'>"
            f"    {''.join(cards)}"
            f"  </div>"
            f"</div>"
        )
        if ri < len(row_codes) - 1:
            rows_html.append("<div class='truck-divider'></div>")

    # Wheels: scale with columns to look proportional
    n_wheels = max(4, n_cols * 2)
    wheels_html = "".join("<div class='wheel'></div>" for _ in range(n_wheels))

    body_inner = (
        "<div class='truck-cabin'>"
        "  <div class='truck-cabin-icon'>🚛</div>"
        f"  <div class='truck-cabin-label'>CABINA · {escape(truck_code)}</div>"
        "</div>"
        "<div class='truck-cargo'>"
        f"  {''.join(rows_html)}"
        "  <div class='truck-tail'>↓ RAMPA TRASERA · descarga principal</div>"
        "</div>"
        f"<div class='truck-wheels'>{wheels_html}</div>"
    )

    if has_toldos:
        toldo_izq_html = _build_zone_card_html(zones.get("toldo_izq", {}), "Toldo izq.")
        toldo_der_html = _build_zone_card_html(zones.get("toldo_der", {}), "Toldo der.")
        return f"""
        <div class="truck-schema">
          <div class="truck-side">
            <div class="truck-side-label">⬅ TOLDO IZQUIERDO</div>
            {toldo_izq_html}
            <div class="truck-side-foot">Retornables<br>(acceso lateral deslizable)</div>
          </div>
          <div class="truck-body">{body_inner}</div>
          <div class="truck-side">
            <div class="truck-side-label">TOLDO DERECHO ➡</div>
            {toldo_der_html}
            <div class="truck-side-foot">Retornables<br>(acceso lateral deslizable)</div>
          </div>
        </div>
        """
    # Sin toldos (furgoneta): el cuerpo ocupa todo el ancho
    return f"""
    <div class="truck-schema truck-schema-no-toldos">
      <div class="truck-body">{body_inner}</div>
    </div>
    """


def _build_loading_sequence_html(plan: dict) -> str:
    sequence = plan.get("warehouse_preparation", []) or []
    if not sequence:
        return ""

    cards = []
    total = len(sequence)
    for i, entry in enumerate(sequence):
        position = i + 1
        route_order = entry.get("route_order", "?")
        cliente = escape(str(entry.get("cliente_nombre", "—")))
        poblacion = escape(str(entry.get("poblacion", "")))

        seq_lines = []
        for seq in entry.get("picking_sequence", []) or []:
            uma = escape(str(seq.get("uma", "?")))
            bahia = escape(str(seq.get("bahia", "—")))
            vol = float(seq.get("volumen_l", 0) or 0)
            kg = float(seq.get("peso_kg", 0) or 0)
            seq_lines.append(
                f"<li><span class='seq-uma'>{uma}</span> → <span class='seq-bay'>{bahia}</span>"
                f" <span class='seq-meta'>{vol:.0f} L · {kg:.0f} kg</span></li>"
            )
        if not seq_lines:
            seq_lines.append("<li class='seq-empty'>Sin productos</li>")

        cards.append(
            f"<div class='seq-step'>"
            f"<div class='seq-step-num'>{position}</div>"
            f"<div class='seq-step-body'>"
            f"<div class='seq-step-title'>{cliente}</div>"
            f"<div class='seq-step-sub'>Parada #{route_order} · {poblacion}</div>"
            f"<ul class='seq-step-list'>{''.join(seq_lines)}</ul>"
            f"</div>"
            f"</div>"
        )

    return f"""
    <div class="seq-card">
      <div class="seq-head">
        <div class="seq-title">📋 Orden de picking en almacén</div>
        <div class="seq-sub">Carga LIFO · {total} pasos · El primer cliente de la ruta se carga el último</div>
      </div>
      <div class="seq-list">{''.join(cards)}</div>
    </div>
    """


def _build_zone_details_html(plan: dict) -> str:
    zones = plan.get("loading_zones", []) or []
    if not zones:
        return ""

    rows = []
    for z in zones:
        items = z.get("items", []) or []
        zone_name = escape(str(z.get("zone_name", z.get("zone_id", ""))))
        cap = float(z.get("capacity_l", 0) or 0)
        vol = float(z.get("current_volume_l", 0) or 0)
        pct = (vol / cap * 100) if cap else 0
        access = escape(str(z.get("access", "")))
        ratio = (vol / cap) if cap else 0.0
        bar_color = _zone_fill_color(ratio)

        if not items:
            content_html = "<div class='zd-empty'>Sin items asignados</div>"
        else:
            chips = []
            for it in sorted(items, key=lambda x: float(x.get("vol_l", 0) or 0), reverse=True):
                name = escape(str(it.get("name", "—")))
                ivol = float(it.get("vol_l", 0) or 0)
                ikg = float(it.get("peso_kg", 0) or 0)
                cant = int(it.get("cantidad", 0) or 0)
                stops = it.get("stops", []) or []
                stops_label = ", ".join(f"#{s}" for s in stops[:6])
                if len(stops) > 6:
                    stops_label += f" +{len(stops) - 6}"
                ret_badge = "<span class='zd-tag zd-tag-ret'>♻️ Retornable</span>" if it.get("retornable") else ""
                chips.append(
                    f"<div class='zd-item'>"
                    f"<div class='zd-item-name'>{name} {ret_badge}</div>"
                    f"<div class='zd-item-meta'>{cant} uds · {ivol:.0f} L · {ikg:.0f} kg</div>"
                    f"<div class='zd-item-stops'>Paradas: {stops_label or '—'}</div>"
                    f"</div>"
                )
            content_html = f"<div class='zd-grid'>{''.join(chips)}</div>"

        rows.append(
            f"<div class='zd-zone'>"
            f"<div class='zd-zone-head'>"
            f"<div class='zd-zone-name'>{zone_name}</div>"
            f"<div class='zd-zone-meta'>{vol:.0f} / {cap:.0f} L · <b style='color:{bar_color}'>{pct:.0f}%</b> · {access}</div>"
            f"</div>"
            f"<div class='zd-zone-bar'><div class='zd-zone-fill' style='width:{min(100, pct):.1f}%; background:{bar_color};'></div></div>"
            f"{content_html}"
            f"</div>"
        )

    return f"""
    <div class="zd-card">
      <div class="zd-title">📦 Contenido detallado por zona</div>
      {''.join(rows)}
    </div>
    """


def _build_safety_notes_html(plan: dict) -> str:
    notes = plan.get("safety_notes", []) or []
    items = []
    for n in notes:
        text = str(n or "").strip()
        if not text:
            continue
        cls = "sn-ok"
        if "ALERTA" in text or text.startswith("!"):
            cls = "sn-alert"
        elif "⚠" in text or "casi" in text.lower():
            cls = "sn-warn"
        items.append(f"<li class='{cls}'>{escape(text)}</li>")
    if not items:
        return ""
    return f"""
    <div class="sn-card">
      <div class="sn-title">⚠️ Notas de seguridad y carga</div>
      <ul class="sn-list">{''.join(items)}</ul>
    </div>
    """


_LOADING_PLAN_CSS = """
<style>
  .lp-shell { font-family: Inter, system-ui, sans-serif; color: #0f172a; }
  .lp-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 0.7rem; margin-bottom: 1rem; }
  .lp-kpi { background: white; border: 1px solid #e2e8f0; border-radius: 14px; padding: 0.75rem 0.9rem; box-shadow: 0 4px 14px rgba(15,23,42,0.05); }
  .lp-kpi-label { color: #64748b; font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }
  .lp-kpi-value { font-size: 1.55rem; font-weight: 800; margin-top: 0.15rem; color: #0f172a; }
  .lp-kpi-cap { color: #475569; font-size: 0.78rem; margin-top: 0.1rem; }

  .truck-schema { display: grid; grid-template-columns: 170px 1fr 170px; gap: 0.8rem; align-items: stretch; margin-bottom: 1.4rem; }
  .truck-schema-no-toldos { grid-template-columns: 1fr; }
  .truck-side { display: flex; flex-direction: column; background: linear-gradient(180deg, #fef3c7 0%, #fde68a 100%); border: 2px dashed #f59e0b; border-radius: 16px; padding: 0.6rem; gap: 0.5rem; }
  .truck-side-label { font-size: 0.72rem; font-weight: 800; color: #92400e; text-align: center; letter-spacing: 0.05em; }
  .truck-side-foot { font-size: 0.7rem; color: #92400e; text-align: center; margin-top: auto; line-height: 1.25; }

  .truck-body { display: flex; flex-direction: column; background: #f1f5f9; border-radius: 18px; border: 2px solid #334155; padding: 0.5rem; gap: 0.4rem; position: relative; }
  .truck-cabin { display: flex; align-items: center; justify-content: center; gap: 0.5rem; background: linear-gradient(135deg, #E20613 0%, #b91c1c 100%); color: white; border-radius: 12px 12px 4px 4px; padding: 0.55rem; font-weight: 800; }
  .truck-cabin-icon { font-size: 1.4rem; }
  .truck-cabin-label { font-size: 0.85rem; letter-spacing: 0.12em; }
  .truck-cargo { background: white; border-radius: 10px; padding: 0.5rem; display: flex; flex-direction: column; gap: 0.4rem; }
  .truck-row { display: grid; grid-template-columns: 28px 1fr; gap: 0.4rem; align-items: stretch; }
  .truck-row-label { writing-mode: vertical-rl; transform: rotate(180deg); display: flex; align-items: center; justify-content: center; background: #1e293b; color: white; border-radius: 6px; font-weight: 800; font-size: 0.78rem; letter-spacing: 0.12em; }
  .truck-row-content { display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; }
  .truck-divider { height: 0; border-top: 2px dashed #94a3b8; margin: 0.1rem 0; }
  .truck-tail { text-align: center; font-size: 0.72rem; font-weight: 700; color: #475569; padding-top: 0.45rem; border-top: 2px solid #94a3b8; margin-top: 0.3rem; }
  .truck-wheels { display: flex; justify-content: space-around; padding: 0 2rem; margin-top: -0.3rem; }
  .wheel { width: 26px; height: 26px; border-radius: 50%; background: #1e293b; border: 3px solid #475569; box-shadow: 0 2px 4px rgba(0,0,0,0.25); }

  .zone-card { background: white; border: 1px solid #e2e8f0; border-left: 5px solid var(--accent, #94a3b8); border-radius: 10px; padding: 0.55rem 0.7rem; display: flex; flex-direction: column; gap: 0.35rem; min-height: 120px; }
  .zone-card-head { display: flex; justify-content: space-between; align-items: center; gap: 0.4rem; }
  .zone-card-label { font-size: 0.74rem; font-weight: 800; color: #0f172a; line-height: 1.1; }
  .zone-card-pct { font-size: 0.95rem; font-weight: 800; }
  .zone-card-bar { width: 100%; height: 6px; background: #e2e8f0; border-radius: 999px; overflow: hidden; }
  .zone-card-fill { height: 100%; transition: width 0.3s; }
  .zone-card-meta { display: flex; justify-content: space-between; font-size: 0.72rem; color: #475569; }
  .zone-card-top { font-size: 0.74rem; color: #0f172a; font-weight: 600; padding: 0.22rem 0.4rem; background: #f1f5f9; border-radius: 6px; text-align: center; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .zone-card-foot { font-size: 0.68rem; color: #64748b; text-align: right; }

  .seq-card { background: white; border: 1px solid #e2e8f0; border-radius: 16px; padding: 1rem; box-shadow: 0 8px 18px rgba(15,23,42,0.05); margin-bottom: 1rem; }
  .seq-head { margin-bottom: 0.8rem; }
  .seq-title { font-size: 1.05rem; font-weight: 800; color: #0f172a; }
  .seq-sub { color: #64748b; font-size: 0.8rem; margin-top: 0.15rem; }
  .seq-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 0.7rem; }
  .seq-step { display: flex; gap: 0.6rem; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 0.6rem 0.7rem; }
  .seq-step-num { width: 32px; height: 32px; min-width: 32px; border-radius: 50%; background: #E20613; color: white; font-weight: 800; display: flex; align-items: center; justify-content: center; font-size: 0.85rem; }
  .seq-step-body { flex: 1; min-width: 0; }
  .seq-step-title { font-weight: 800; font-size: 0.85rem; color: #0f172a; }
  .seq-step-sub { font-size: 0.72rem; color: #64748b; margin-bottom: 0.3rem; }
  .seq-step-list { margin: 0; padding-left: 1.1rem; font-size: 0.74rem; color: #334155; line-height: 1.45; }
  .seq-uma { background: #fee2e2; color: #991b1b; padding: 0 0.35rem; border-radius: 5px; font-weight: 800; font-size: 0.68rem; }
  .seq-bay { background: #dbeafe; color: #1e3a8a; padding: 0 0.35rem; border-radius: 5px; font-weight: 700; font-size: 0.68rem; }
  .seq-meta { color: #64748b; font-size: 0.7rem; margin-left: 0.2rem; }
  .seq-empty { color: #94a3b8; font-style: italic; }

  .zd-card { background: white; border: 1px solid #e2e8f0; border-radius: 16px; padding: 1rem; box-shadow: 0 8px 18px rgba(15,23,42,0.05); margin-bottom: 1rem; }
  .zd-title { font-size: 1.05rem; font-weight: 800; color: #0f172a; margin-bottom: 0.7rem; }
  .zd-zone { border-top: 1px solid #e2e8f0; padding: 0.8rem 0; }
  .zd-zone:first-of-type { border-top: none; padding-top: 0; }
  .zd-zone-head { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.4rem; flex-wrap: wrap; gap: 0.3rem; }
  .zd-zone-name { font-weight: 800; color: #0f172a; font-size: 0.92rem; }
  .zd-zone-meta { font-size: 0.78rem; color: #64748b; }
  .zd-zone-bar { width: 100%; height: 5px; background: #e2e8f0; border-radius: 999px; overflow: hidden; margin-bottom: 0.6rem; }
  .zd-zone-fill { height: 100%; }
  .zd-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.5rem; }
  .zd-item { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 0.5rem 0.6rem; }
  .zd-item-name { font-weight: 700; font-size: 0.78rem; color: #0f172a; }
  .zd-item-meta { font-size: 0.72rem; color: #475569; margin-top: 0.15rem; }
  .zd-item-stops { font-size: 0.7rem; color: #64748b; margin-top: 0.2rem; }
  .zd-empty { color: #94a3b8; font-size: 0.78rem; font-style: italic; padding: 0.4rem 0; }
  .zd-tag { display: inline-block; padding: 0 0.35rem; border-radius: 5px; font-size: 0.65rem; font-weight: 700; margin-left: 0.3rem; }
  .zd-tag-ret { background: #dcfce7; color: #166534; }

  .sn-card { background: white; border: 1px solid #e2e8f0; border-radius: 16px; padding: 1rem; box-shadow: 0 8px 18px rgba(15,23,42,0.05); }
  .sn-title { font-size: 1.05rem; font-weight: 800; color: #0f172a; margin-bottom: 0.5rem; }
  .sn-list { margin: 0; padding-left: 1.2rem; font-size: 0.85rem; line-height: 1.55; }
  .sn-list li { margin-bottom: 0.2rem; }
  .sn-ok { color: #166534; }
  .sn-warn { color: #92400e; }
  .sn-alert { color: #991b1b; font-weight: 700; }
</style>
"""


def _build_loading_maps_html(plan: dict, truck_code: str, truck_capacity_l: int) -> str:
    """Plan de carga visual claro: esquema del camión real (4 bahías + 2 toldos),
    secuencia de picking LIFO, detalle por zona y notas de seguridad."""
    zones = plan.get("loading_zones", []) or []
    total_vol = sum(float(z.get("current_volume_l", 0) or 0) for z in zones)
    total_weight = sum(float(it.get("peso_kg", 0) or 0) for z in zones for it in (z.get("items") or []))
    n_items = sum(len(z.get("items") or []) for z in zones)
    util = (total_vol / truck_capacity_l * 100) if truck_capacity_l else 0.0

    bay_loads = [
        float(z.get("current_volume_l", 0) or 0)
        for z in zones
        if str(z.get("zone_id", "")).startswith("bay")
    ]
    if bay_loads and max(bay_loads) > 0:
        balance_pct = (1 - (max(bay_loads) - min(bay_loads)) / max(bay_loads)) * 100
    else:
        balance_pct = 100.0

    schematic = _build_truck_schematic_html(plan)
    sequence = _build_loading_sequence_html(plan)
    details = _build_zone_details_html(plan)
    safety = _build_safety_notes_html(plan)

    return _LOADING_PLAN_CSS + f"""
    <div class="lp-shell">
      <div class="lp-kpis">
        <div class="lp-kpi"><div class="lp-kpi-label">Camión</div><div class="lp-kpi-value">{escape(truck_code)}</div><div class="lp-kpi-cap">{truck_capacity_l:.0f} L de capacidad</div></div>
        <div class="lp-kpi"><div class="lp-kpi-label">Volumen cargado</div><div class="lp-kpi-value">{total_vol:.0f} L</div><div class="lp-kpi-cap">{util:.1f}% utilizado</div></div>
        <div class="lp-kpi"><div class="lp-kpi-label">Peso total</div><div class="lp-kpi-value">{total_weight:.0f} kg</div><div class="lp-kpi-cap">{n_items} líneas de producto</div></div>
        <div class="lp-kpi"><div class="lp-kpi-label">Equilibrio bahías</div><div class="lp-kpi-value">{balance_pct:.0f}%</div><div class="lp-kpi-cap">100 % = peso uniforme</div></div>
      </div>
      {schematic}
      {sequence}
      {details}
      {safety}
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


def _build_loading_3d_figure(stops_data: list[dict], truck_capacity_l: int, truck_code: str = "6P") -> go.Figure:
    plan = visualize_loading_plan(stops_data, truck_capacity_l, truck_code=truck_code)
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
            metrics = snapshot_payload.get("metrics", {}) or {}

            # Normalizar payload: en modo flota cada vehículo tiene su propia ruta.
            is_fleet = "routes" in snapshot_payload and bool(snapshot_payload.get("routes"))
            if is_fleet:
                routes_data = snapshot_payload.get("routes") or []
                stops_data = [s for r in routes_data for s in (r.get("stops") or [])]
            else:
                stops_data = snapshot_payload.get("stops", []) or []
                routes_data = [{
                    "vehicle_index": 1,
                    "stops": stops_data,
                    "entrega_total_l": float(result.get("entrega_total_l", 0) or 0),
                    "recogida_total_l": float(result.get("recogida_total_l", 0) or 0),
                    "distance_m": float(result.get("opt_dist_m", 0) or 0),
                    "time_s": float(result.get("opt_time_s", 0) or 0),
                    "n_stops": int(result.get("n_stops", 0) or 0),
                }]

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

                if not stops_data:
                    st.info("ℹ️ No hay datos de ruta disponibles")
                elif is_fleet and len(routes_data) > 1:
                    st.caption(
                        f"Mapa multi-vehículo: {len(routes_data)} rutas, una por camión. "
                        "Usa el control de capas (arriba a la derecha) para mostrar/ocultar vehículos."
                    )
                    st.components.v1.html(_build_multi_route_map_html(routes_data), height=720, scrolling=False)

                    legend_chips = "".join(
                        f"<span style='display:inline-flex;align-items:center;gap:0.35rem;"
                        f"margin:0 0.6rem 0.4rem 0;font-size:0.85rem;font-weight:600;color:#0f172a;'>"
                        f"<span style='width:14px;height:14px;border-radius:50%;background:{_vehicle_color(int(r.get('vehicle_index', i+1)))};"
                        "border:2px solid white;box-shadow:0 1px 3px rgba(0,0,0,0.25);'></span>"
                        f"Vehículo {int(r.get('vehicle_index', i+1))} · {len(r.get('stops') or [])} paradas"
                        "</span>"
                        for i, r in enumerate(routes_data)
                    )
                    st.markdown(f"<div style='margin-top:0.6rem;'>{legend_chips}</div>", unsafe_allow_html=True)

                    with st.expander("Ver tablas detalladas por vehículo", expanded=False):
                        sub_tabs = st.tabs([
                            f"🚚 V{int(r.get('vehicle_index', i+1))} ({len(r.get('stops') or [])} pdas)"
                            for i, r in enumerate(routes_data)
                        ])
                        for sub, r in zip(sub_tabs, routes_data):
                            with sub:
                                v_stops = r.get("stops") or []
                                if v_stops:
                                    st.markdown(_build_route_table_html(v_stops), unsafe_allow_html=True)
                                else:
                                    st.info("Vehículo sin paradas asignadas.")
                else:
                    st.caption("Mapa interactivo estilo Google Maps + tabla completa con entregas y recogidas concretas.")
                    st.components.v1.html(_build_route_map_html(stops_data), height=720, scrolling=False)
                    with st.expander("Ver ruta completa en detalle", expanded=True):
                        st.markdown(_build_route_table_html(stops_data), unsafe_allow_html=True)

            with tab3:
                st.markdown('<div class="section-title">Plan de Carga</div>', unsafe_allow_html=True)
                truck_capacity_l = int(TRUCKS[truck_selected]["vol_m3"] * 1000)
                import streamlit.components.v1 as components

                if not stops_data:
                    st.info("ℹ️ No hay datos de carga para visualizar")
                elif is_fleet and len(routes_data) > 1:
                    st.caption(
                        f"Cada vehículo tiene su propio camión {truck_selected} y su propio plan de carga. "
                        "Selecciona la pestaña del vehículo para verlo."
                    )
                    sub_tabs = st.tabs([
                        f"🚚 V{int(r.get('vehicle_index', i+1))} ({len(r.get('stops') or [])} pdas)"
                        for i, r in enumerate(routes_data)
                    ])
                    for i, (sub, r) in enumerate(zip(sub_tabs, routes_data)):
                        with sub:
                            v_stops = r.get("stops") or []
                            if not v_stops:
                                st.info("Este vehículo no tiene paradas asignadas.")
                                continue
                            v_idx = int(r.get("vehicle_index", i + 1))
                            v_color = _vehicle_color(v_idx)
                            v_entrega = float(r.get("entrega_total_l", 0) or 0)
                            v_recogida = float(r.get("recogida_total_l", 0) or 0)
                            v_dist_km = float(r.get("distance_m", 0) or 0) / 1000
                            v_time_h = float(r.get("time_s", 0) or 0) / 3600
                            st.markdown(
                                f"<div style='display:flex;align-items:center;gap:0.5rem;margin-bottom:0.4rem;'>"
                                f"<span style='width:14px;height:14px;border-radius:50%;background:{v_color};"
                                "border:2px solid white;box-shadow:0 1px 3px rgba(0,0,0,0.25);'></span>"
                                f"<b style='font-size:0.95rem;color:#0f172a;'>Vehículo {v_idx}</b>"
                                f"<span style='color:#64748b;font-size:0.85rem;'>· {len(v_stops)} paradas · "
                                f"{v_entrega:.0f} L entrega · {v_recogida:.0f} L recogida · "
                                f"{v_dist_km:.1f} km · {v_time_h:.1f} h</span>"
                                "</div>",
                                unsafe_allow_html=True,
                            )
                            v_plan = visualize_loading_plan(v_stops, truck_capacity_l, truck_code=truck_selected)
                            v_html = _build_loading_maps_html(v_plan, truck_selected, truck_capacity_l)
                            components.html(v_html, height=720, scrolling=True)
                else:
                    loading_plan = visualize_loading_plan(stops_data, truck_capacity_l, truck_code=truck_selected)
                    html_blob = _build_loading_maps_html(loading_plan, truck_selected, truck_capacity_l)
                    components.html(html_blob, height=720, scrolling=True)
                    st.caption(f"Plan de carga del {truck_selected}: distribución dinámica por bahías y toldos.")

                st.markdown(_build_loading_overview_html(result), unsafe_allow_html=True)

                # === Estrategia híbrida (req #3): explicar el modelo de carga ===
                st.divider()
                st.markdown("### 🧩 Estrategia híbrida de carga (referencia ↔ cliente)")
                # Métricas de coherencia: cuántos clientes tienen TODA su carga en una sola bahía
                if stops_data:
                    n_clients = len({s.get("cliente_id") for s in stops_data})
                    n_stops = len(stops_data)
                    n_repeat = n_stops - n_clients
                    repeat_pct = (n_repeat / n_stops * 100) if n_stops else 0
                    n_micro = sum(1 for s in stops_data if float(s.get("entrega_l", 0) or 0) < 100)
                    micro_pct = (n_micro / n_stops * 100) if n_stops else 0
                    if micro_pct > 30:
                        modelo, color = "Por referencia", "#0ea5e9"
                        razon = "Muchas paradas micro: agrupar por SKU al cargar reduce movimientos."
                    elif n_clients < 8:
                        modelo, color = "Por cliente", "#16a34a"
                        razon = "Pocos clientes con grandes volúmenes: una bahía por cliente facilita descarga."
                    else:
                        modelo, color = "Híbrido (recomendado)", "#9333ea"
                        razon = ("Mezcla óptima: clusters geográficos comparten bahías por SKU, "
                                 "clientes grandes ocupan bahías propias por cliente.")
                    st.markdown(
                        f"""
                        <div style='background:linear-gradient(135deg,#f8fafc,white);border-left:5px solid {color};
                        border-radius:12px;padding:0.8rem 1rem;margin-top:0.5rem;'>
                          <div style='font-weight:800;color:{color};font-size:1.05rem;'>Modelo sugerido: {modelo}</div>
                          <div style='color:#475569;font-size:0.88rem;margin-top:0.25rem;'>{razon}</div>
                          <div style='display:flex;gap:1rem;margin-top:0.6rem;flex-wrap:wrap;font-size:0.82rem;color:#0f172a;'>
                            <div>📍 <b>{n_clients}</b> clientes únicos · <b>{n_stops}</b> paradas</div>
                            <div>🔁 <b>{repeat_pct:.0f}%</b> entregas duplicadas a mismo cliente</div>
                            <div>📦 <b>{n_micro}</b> micro-paradas (&lt;100 L) · <b>{micro_pct:.0f}%</b></div>
                          </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with st.expander("📖 ¿Por qué jerarquía clúster → bahía → cliente → referencia?", expanded=False):
                    st.markdown(
                        """
- **1. Clúster geográfico** → asignar bloque de bahías por barrio/población.
  Razón: la lona lateral se abre 1 vez por clúster, no por cliente.
- **2. Una bahía por cliente** dentro del clúster.
  Razón: el repartidor descarga sin tocar carga de otros clientes (acceso lateral lo permite).
- **3. Dentro de la bahía, ordenado por SKU** (referencia).
  Razón: el albarán del chófer ya viene agrupado por SKU; menos errores.
- **4. Apilamiento por estabilidad**: barriles abajo, cajas medio, frágiles arriba.

Esta jerarquía maximiza dos eficiencias a la vez:
- **Picking en almacén** (agrupar por SKU = menos paseos)
- **Descarga en cliente** (agrupar por cliente = menos movimientos)

El packer del proyecto (`src/packer.py` + `src/loading_visualization.py`) implementa esta jerarquía.
                        """
                    )

            with tab4:
                st.markdown('<div class="section-title">Análisis e Insights automáticos</div>', unsafe_allow_html=True)
                total_entrega = float(result.get("entrega_total_l", 0) or 0)
                total_recogida = float(result.get("recogida_total_l", 0) or 0)
                peso_total = float(result.get("peso_kg", 0) or 0)
                truck_cap_l = int(TRUCKS[truck_selected]["vol_m3"] * 1000)
                truck_cap_kg = int(TRUCKS[truck_selected]["peso_max_kg"])

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

                # === Llamada al motor de insights real ===
                try:
                    insight = analyze_route(
                        transporte_id=int(transport_id),
                        stops=stops_data,
                        baseline_time_s=int(result.get("baseline_time_s", 0) or 0),
                        baseline_dist_m=int(result.get("baseline_dist_m", 0) or 0),
                        opt_time_s=int(result.get("opt_time_s", 0) or 0),
                        opt_dist_m=int(result.get("opt_dist_m", 0) or 0),
                        truck_capacity_l=truck_cap_l,
                        truck_capacity_kg=truck_cap_kg,
                        total_entrega_l=total_entrega,
                        total_recogida_l=total_recogida,
                    )
                except Exception as e:
                    insight = None
                    st.warning(f"No se pudieron calcular insights detallados: {e}")

                if insight:
                    st.divider()
                    st.subheader("🎯 Criterios priorizados por el optimizador")
                    chips = " ".join(
                        f"<span class='badge' style='background:#1e40af;'>{escape(c)}</span>"
                        for c in insight.optimization_criteria
                    )
                    st.markdown(f"<div>{chips}</div>", unsafe_allow_html=True)

                    if insight.cluster_recommendations:
                        st.divider()
                        st.subheader("👥 Oportunidades de agrupación")
                        for c in insight.cluster_recommendations[:6]:
                            prio = c.get("priority", "medium")
                            color = {"high": "#dc2626", "medium": "#f59e0b", "low": "#64748b"}.get(prio, "#64748b")
                            ctype = "🌍 Cluster geográfico" if c.get("type") == "geographic_cluster" else "🔁 Cliente repetido"
                            extra = (f"📍 {c.get('location', '')} · {c.get('stops', '')} paradas · "
                                     f"{c.get('volume_l', 0):.0f}L"
                                     if c.get("type") == "geographic_cluster"
                                     else f"👤 {c.get('cliente_nombre', '')} · {c.get('visits', '')} visitas · "
                                          f"paradas {c.get('orders', [])}")
                            st.markdown(
                                f"<div style='background:white;border-left:4px solid {color};border-radius:8px;"
                                f"padding:0.6rem 0.8rem;margin-bottom:0.4rem;box-shadow:0 2px 6px rgba(0,0,0,0.04);'>"
                                f"<b>{ctype}</b> · <span style='color:{color};font-weight:700;'>{prio.upper()}</span><br>"
                                f"<span style='color:#64748b;font-size:0.85rem;'>{escape(extra)}</span><br>"
                                f"<span style='font-size:0.85rem;'>{escape(c.get('note', ''))}</span>"
                                f"</div>",
                                unsafe_allow_html=True,
                            )

                    col_f, col_e = st.columns(2)
                    with col_f:
                        st.subheader("⚠️ Puntos de fricción")
                        for fp in insight.friction_points[:8]:
                            icon = "🔴" if "ALERTA" in fp else "🟡"
                            st.markdown(f"{icon} {fp}")
                    with col_e:
                        st.subheader("⚡ Alertas de eficiencia")
                        for ea in insight.efficiency_alerts[:8]:
                            icon = "🔴" if "Alto" in ea or "ALERTA" in ea else "🟢"
                            st.markdown(f"{icon} {ea}")

                    st.divider()
                    st.subheader("🚀 Recomendaciones accionables")
                    if insight.recommendations:
                        for rec in insight.recommendations:
                            prio = rec.get("priority", "medium")
                            color = {"high": "#dc2626", "medium": "#f59e0b", "low": "#16a34a"}.get(prio, "#64748b")
                            with st.expander(
                                f"{'🔴' if prio == 'high' else '🟡' if prio == 'medium' else '🟢'} "
                                f"[{prio.upper()}] {rec.get('description', '')}",
                                expanded=(prio == "high"),
                            ):
                                st.markdown(f"**Detalle:** {rec.get('details', '')}")
                                st.markdown(f"**Impacto:** {rec.get('impact', '')}")
                                st.caption(f"action_id: `{rec.get('action', '')}`")
                    else:
                        st.info("Sin recomendaciones accionables para esta ruta.")

                # === Recomendación de Layout del Almacén (req #7) ===
                st.divider()
                st.subheader("🏭 Layout del almacén — propuesta de reorganización")
                st.caption(
                    "Basado en frecuencia y volumen reales de SKUs en TODO el dataset histórico. "
                    "Mover los más frecuentes pegados al muelle reduce minutos de picking por ruta."
                )
                try:
                    layout_recs = recommend_warehouse_layout(top_k=20)
                except Exception as e:
                    layout_recs = []
                    st.warning(f"No se pudo calcular layout del almacén: {e}")

                if layout_recs:
                    # Distribución por zona
                    zone_counts: dict[str, int] = {}
                    total_ahorro = 0.0
                    for r in layout_recs:
                        zone_counts[r.zona_recomendada] = zone_counts.get(r.zona_recomendada, 0) + 1
                        total_ahorro += r.ahorro_estimado_min
                    zone_chips = "".join(
                        f"<span class='badge' style='background:{'#16a34a' if z=='muelle' else '#f59e0b' if z=='intermedia' else '#0ea5e9' if z=='media' else '#64748b'};'>"
                        f"{z}: {n}</span> "
                        for z, n in zone_counts.items()
                    )
                    st.markdown(
                        f"<div style='margin-bottom:0.6rem;'>{zone_chips}"
                        f"<span style='margin-left:0.5rem;color:#64748b;font-size:0.85rem;'>"
                        f"Ahorro estimado total: <b>{total_ahorro:.0f} min</b> sobre el histórico"
                        "</span></div>",
                        unsafe_allow_html=True,
                    )
                    st.dataframe(
                        pd.DataFrame([
                            {
                                "Material": r.material,
                                "Descripción": r.denominacion[:50],
                                "UMA": r.uma,
                                "Aparece en": f"{r.frecuencia_transportes} ({r.frecuencia_pct*100:.1f}%)",
                                "Vol. histórico": f"{r.volumen_total_l:.0f} L",
                                "Zona recomendada": r.zona_recomendada,
                                "Ahorro/ruta (min)": f"{r.ahorro_estimado_min:.1f}",
                            }
                            for r in layout_recs
                        ]),
                        use_container_width=True,
                        hide_index=True,
                    )

                    # Picking path para la ruta actual (LIFO)
                    if stops_data:
                        path = picking_path_for_route(stops_data, layout_recs)
                        st.markdown(
                            f"**Picking estimado para ESTA ruta:** "
                            f"{path['total_lines']} líneas · "
                            f"{path['estimated_pick_time_min']} min "
                            f"(media {path['avg_min_per_line']} min/línea)"
                        )

                # === Tablas de entrega vs recogida (mantener) ===
                st.divider()
                st.subheader("📌 Qué se entrega y qué se recoge")
                all_materials = [m for s in stops_data for m in (s.get("materiales", []) or [])]
                col_a, col_b = st.columns(2)
                with col_a:
                    entrega_agg = _group_materials(all_materials, retornable=False)
                    st.markdown("**Entregas (a bahías)**")
                    if entrega_agg:
                        st.dataframe(
                            pd.DataFrame([
                                {
                                    "Material": x["material"],
                                    "Descripción": x["denominacion"][:40],
                                    "Uds": x["cantidad"],
                                    "Vol": f"{x['vol_l']:.0f} L",
                                    "Peso": f"{x['peso_kg']:.0f} kg",
                                } for x in entrega_agg
                            ]),
                            use_container_width=True, hide_index=True,
                        )
                    else:
                        st.info("Sin entregas registradas")
                with col_b:
                    recogida_agg = _group_materials(all_materials, retornable=True)
                    st.markdown("**Recogidas (a toldos)**")
                    if recogida_agg:
                        st.dataframe(
                            pd.DataFrame([
                                {
                                    "Material": x["material"],
                                    "Descripción": x["denominacion"][:40],
                                    "Uds": x["cantidad"],
                                    "Vol": f"{x['vol_l']:.0f} L",
                                    "Peso": f"{x['peso_kg']:.0f} kg",
                                } for x in recogida_agg
                            ]),
                            use_container_width=True, hide_index=True,
                        )
                    else:
                        st.info("Sin recogidas registradas (toldos vacíos)")

                st.caption(f"Peso total calculado: {peso_total:.0f} kg · Capacidad camión: {truck_cap_kg} kg")

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