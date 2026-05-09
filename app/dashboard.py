"""
Dashboard interactivo de Streamlit para Damm Smart Truck.
Muestra mapa de ruta, carga, insights y detalles técnicos.
"""

from html import escape
from pathlib import Path
import sys

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


st.set_page_config(
    page_title="Damm Smart Truck - Optimizer",
    page_icon="🚚",
    layout="wide",
    initial_sidebar_state="expanded",
)


st.markdown(
    """
<style>
    .main-header {
        font-size: 2.4rem;
        font-weight: 800;
        color: #0f172a;
        letter-spacing: -0.02em;
        margin-bottom: 0.25rem;
    }
    .sub-header {
        color: #475569;
        margin-top: 0;
        margin-bottom: 1rem;
    }
    .hero-card {
        background: linear-gradient(135deg, #eff6ff 0%, #f8fafc 100%);
        border: 1px solid #dbeafe;
        border-radius: 18px;
        padding: 1rem 1.1rem;
        box-shadow: 0 10px 25px rgba(15, 23, 42, 0.05);
        margin-bottom: 0.75rem;
    }
    .section-title {
        font-size: 1.15rem;
        font-weight: 700;
        color: #0f172a;
        margin-top: 0.4rem;
        margin-bottom: 0.4rem;
    }
    .muted { color: #64748b; }
    .badge {
        display: inline-block;
        color: white;
        padding: 0.2rem 0.55rem;
        border-radius: 999px;
        font-size: 0.72rem;
        font-weight: 700;
        margin-right: 0.35rem;
    }
    .route-wrap { width: 100%; }
    .route-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 0.92rem;
        table-layout: fixed;
    }
    .route-table th, .route-table td {
        border: 1px solid #e2e8f0;
        vertical-align: top;
        padding: 0.7rem;
    }
    .route-table th {
        background: #0f172a;
        color: white;
        text-align: left;
    }
    .route-table tr:nth-child(even) { background: #f8fafc; }
    .route-table td.num {
        text-align: center;
        font-weight: 800;
        width: 48px;
    }
    .item-section { margin-bottom: 0.2rem; }
    .section-head { margin-bottom: 0.45rem; }
    .item-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
        gap: 0.45rem;
    }
    .item-chip {
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 14px;
        padding: 0.55rem 0.65rem;
        box-shadow: 0 4px 10px rgba(15, 23, 42, 0.04);
    }
    .item-title {
        font-weight: 800;
        color: #0f172a;
        font-size: 0.88rem;
    }
    .item-desc {
        color: #334155;
        font-size: 0.8rem;
        margin-top: 0.15rem;
    }
    .item-meta {
        color: #64748b;
        font-size: 0.78rem;
        margin-top: 0.2rem;
    }
    .item-empty {
        display: flex;
        flex-direction: column;
        gap: 0.25rem;
    }
    .item-muted { color: #94a3b8; font-size: 0.82rem; }
    .map-card {
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 18px;
        padding: 0.4rem;
        box-shadow: 0 10px 25px rgba(15, 23, 42, 0.05);
    }
    .kpi-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
        gap: 0.8rem;
    }
    .kpi {
        background: white;
        border: 1px solid #e2e8f0;
        border-radius: 16px;
        padding: 0.9rem;
        box-shadow: 0 8px 18px rgba(15, 23, 42, 0.05);
    }
    .kpi .label { color: #64748b; font-size: 0.78rem; font-weight: 700; }
    .kpi .value { font-size: 1.55rem; font-weight: 800; color: #0f172a; margin-top: 0.15rem; }
    .kpi .caption { color: #475569; font-size: 0.8rem; margin-top: 0.2rem; }
</style>
""",
    unsafe_allow_html=True,
)


st.markdown('<div class="main-header">🚚 Damm Smart Truck - Optimizer</div>', unsafe_allow_html=True)
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


if st.sidebar.button("▶️ RESOLVER OPTIMIZACIÓN", key="solve_btn"):
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
                    fig_3d = _build_loading_3d_figure(stops_data, truck_capacity_l)
                    st.plotly_chart(fig_3d, use_container_width=True)
                    st.caption("Vista 3D interactiva: rotar, zoom y pane para inspeccionar la distribución de carga.")
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