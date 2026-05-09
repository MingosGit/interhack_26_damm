"""Damm Smart Truck — Dashboard Streamlit (MVP 9).

Tres páginas:
    1. Inicio — explicación + KPIs agregados sobre los datos completos.
    2. Optimizar — selecciona transporte, ejecuta VRP + packing, muestra mapa,
       camión 3D, tabla de paradas, comparación contra baseline real.
    3. Explorar — gráficas descriptivas del dataset.

Lanzar:
    streamlit run app/dashboard.py
"""
from __future__ import annotations

import sys
from datetime import date as _date
from pathlib import Path

import folium
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_folium import st_folium

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import (                                          # noqa: E402
    config,
    distance_matrix,
    kpis,
    packer,
    reverse_logistics as rl,
    vrp_solver,
)


# ---------------------------------------------------------------------------
# Configuración de página
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Damm Smart Truck",
    page_icon="🚚",
    layout="wide",
)


# ---------------------------------------------------------------------------
# Carga cacheada de datos
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def load_canonical() -> pd.DataFrame:
    return pd.read_parquet(config.CANONICAL_PARQUET)


@st.cache_data(show_spinner=False)
def load_geocoding() -> pd.DataFrame:
    return pd.read_parquet(config.GEOCODING_PARQUET)


@st.cache_data(show_spinner=False)
def load_kpi_csv_if_exists() -> pd.DataFrame | None:
    if config.KPI_COMPARISON_CSV.exists():
        return pd.read_csv(config.KPI_COMPARISON_CSV)
    return None


@st.cache_data(show_spinner=False)
def get_matrix_cached(coords_tuple: tuple) -> tuple[np.ndarray, np.ndarray]:
    """Wrapper hashable de distance_matrix.get_matrix."""
    coords = [tuple(c) for c in coords_tuple]
    return distance_matrix.get_matrix(coords)


# ---------------------------------------------------------------------------
# Sidebar — selector de página
# ---------------------------------------------------------------------------

PAGES = ("Inicio", "Optimizar reparto", "Explorar datos")
page = st.sidebar.radio("Página", PAGES, index=0)
st.sidebar.markdown("---")
st.sidebar.caption("INTERHACK BCN 2026 · Damm DDI")


# ===========================================================================
# Página 1 — Inicio
# ===========================================================================

def _page_inicio():
    st.title("🚚 Damm Smart Truck")
    st.markdown(
        "Optimiza conjuntamente **ruta** y **carga** de los camiones de DDI "
        "(Distribución Directa Integral, grupo Damm). Aprovecha las **lonas "
        "laterales** para modelar el camión como bahías independientes, "
        "co-optimiza la **logística inversa** (60 % retornable) y respeta "
        "**ventanas horarias** por cliente."
    )

    canonical = load_canonical()
    n_transp = canonical["transporte"].nunique()
    n_clientes = canonical["cliente_id"].nunique()
    n_paradas = len(canonical)
    vol_total = canonical["volumen_total_l"].sum()
    vol_ret = canonical["volumen_retornable_l"].sum()
    f_min = canonical["fecha"].min().date()
    f_max = canonical["fecha"].max().date()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Transportes", f"{n_transp:,}")
    c2.metric("Clientes únicos", f"{n_clientes:,}")
    c3.metric("Entregas (paradas)", f"{n_paradas:,}")
    c4.metric("Días", f"{(f_max - f_min).days + 1}")

    c5, c6, c7 = st.columns(3)
    c5.metric("Volumen total entregado", f"{vol_total/1000:,.0f} m³")
    c6.metric("% retornable", f"{100 * vol_ret / vol_total:.1f} %")
    c7.metric("Ventana de datos", f"{f_min} → {f_max}")

    st.markdown("---")
    st.subheader("KPIs agregados (vs baseline real)")
    kpi_df = load_kpi_csv_if_exists()
    if kpi_df is None or kpi_df.empty:
        st.info(
            "Aún no hay batch de KPIs ejecutado. Lanzar:\n\n"
            "```bash\npython -m src.kpis batch --time-limit 3 --limit 50\n```"
        )
        return

    means = kpis.aggregate_means(kpi_df)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Δ distancia", f"{means.get('delta_distancia_pct_mean', 0):+.1f} %",
              delta_color="inverse")
    c2.metric("Δ tiempo", f"{means.get('delta_tiempo_pct_mean', 0):+.1f} %",
              delta_color="inverse")
    c3.metric("Δ movimientos descarga",
              f"{means.get('delta_movimientos_pct_mean', 0):+.1f} %",
              delta_color="inverse")
    c4.metric("Δ retornables recogidos",
              f"+{means.get('delta_retornables_pp_mean', 0):.1f} pp")
    st.caption(f"Calculado sobre {means.get('n_transportes', 0)} transportes "
               f"(outliers que empeoran: dist={means.get('n_outliers_distancia_worse', 0)}, "
               f"tiempo={means.get('n_outliers_tiempo_worse', 0)})")

    fig = kpis.plot_summary(kpi_df)
    st.plotly_chart(fig, use_container_width=True)


# ===========================================================================
# Página 2 — Optimizar reparto
# ===========================================================================

def _build_route_map(stops, ordered_stops, depot):
    """Folium con depot, polyline ordenada y markers numerados."""
    m = folium.Map(location=depot, zoom_start=11, tiles="OpenStreetMap",
                   control_scale=True)
    folium.Marker(
        depot,
        tooltip=f"DEPOT — {config.DEPOT_NAME}",
        icon=folium.Icon(color="red", icon="industry", prefix="fa"),
    ).add_to(m)

    coords = [depot] + [(s.lat, s.lng) for s in ordered_stops] + [depot]
    folium.PolyLine(coords, color="#1f77b4", weight=4, opacity=0.7).add_to(m)

    for k, s in enumerate(ordered_stops, 1):
        folium.Marker(
            [s.lat, s.lng],
            tooltip=f"{k}. {s.cliente_nombre}",
            popup=(f"<b>{k}. {s.cliente_nombre}</b><br>"
                   f"{s.calle if hasattr(s, 'calle') else ''}<br>"
                   f"{s.poblacion} {getattr(s, 'cp', '')}<br>"
                   f"<b>{s.volumen_l:.0f} L</b> / {s.peso_kg:.0f} kg<br>"
                   f"retornable: {s.volumen_retornable_l:.0f} L"),
            icon=folium.DivIcon(html=f"""
                <div style="background-color:#1f77b4;color:white;width:28px;
                            height:28px;border-radius:50%;text-align:center;
                            line-height:28px;font-weight:bold;
                            border:2px solid white;box-shadow:0 0 4px #555;">
                  {k}
                </div>
            """),
        ).add_to(m)
    m.fit_bounds([(min(c[0] for c in coords), min(c[1] for c in coords)),
                  (max(c[0] for c in coords), max(c[1] for c in coords))])
    return m


def _format_hms(seconds: int | float) -> str:
    if seconds is None:
        return "—"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s"


def _page_optimizar():
    st.title("🛣️ Optimizar reparto")
    canonical = load_canonical()
    geocoding = load_geocoding()

    # ---- selectores ----
    fechas = sorted(canonical["fecha"].dt.date.unique())
    if not fechas:
        st.error("No hay fechas en canonical.parquet")
        return

    c1, c2, c3 = st.columns(3)
    fecha_sel = c1.selectbox("Fecha", fechas, index=0,
                              format_func=lambda d: d.isoformat())
    sub = canonical[canonical["fecha"].dt.date == fecha_sel]
    repartidores = sorted(sub["repartidor_nombre"].dropna().unique())
    repart_sel = c2.selectbox("Repartidor", repartidores)
    sub_r = sub[sub["repartidor_nombre"] == repart_sel]
    transportes = sorted(sub_r["transporte"].unique())
    transp_sel = c3.selectbox(
        "Transporte", transportes,
        format_func=lambda t: f"{t} ({(sub_r['transporte']==t).sum()} paradas)",
    )

    info = sub_r[sub_r["transporte"] == transp_sel]
    n_par = len(info)
    vol = info["volumen_total_l"].sum()
    vol_ret = info["volumen_retornable_l"].sum()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Paradas", f"{n_par}")
    c2.metric("Volumen", f"{vol:,.0f} L")
    c3.metric("Vol. retornable", f"{vol_ret:,.0f} L")
    c4.metric("Ruta", info["ruta"].iloc[0] if len(info) else "—")

    st.markdown("---")
    c1, c2, c3 = st.columns(3)
    truck = c1.selectbox("Camión", list(config.TRUCKS.keys()), index=0)
    use_tw = c2.checkbox("Aplicar ventanas horarias", value=False)
    use_pd = c3.checkbox("Optimizar logística inversa (pickup)", value=True)
    time_limit = st.slider("Tiempo límite del solver (s)", 5, 60, 15)

    if st.button("🚀 Optimizar", type="primary", use_container_width=True):
        _run_optimization(canonical, geocoding, transp_sel, truck,
                          use_tw, use_pd, time_limit)


def _run_optimization(canonical, geocoding, transp_id, truck,
                      use_tw, use_pd, time_limit):
    with st.spinner("Construyendo paradas y calculando matriz…"):
        try:
            stops = vrp_solver.build_stops_from_transporte(
                transp_id, canonical, geocoding,
            )
        except Exception as exc:                            # noqa: BLE001
            st.error(f"Error al construir paradas: {exc}")
            return

        if not stops:
            st.warning("No hay clientes geocodificados en este transporte.")
            return

        if use_tw:
            fecha = canonical[canonical["transporte"] == transp_id]["fecha"].iloc[0]
            vrp_solver.attach_time_windows(stops, fecha, canonical=canonical)

        coords = [(config.DEPOT_LAT, config.DEPOT_LNG)] + [(s.lat, s.lng) for s in stops]
        time_mat, dist_mat = get_matrix_cached(tuple(coords))

    with st.spinner("Resolviendo VRP con OR-Tools…"):
        cap_l = config.TRUCKS[truck]["vol_m3"] * 1000.0
        cap_kg = float(config.TRUCKS[truck]["peso_max_kg"])
        base_t, base_d = vrp_solver.baseline_metrics(stops, time_mat, dist_mat)
        sol = vrp_solver.solve_single_truck(
            stops, (config.DEPOT_LAT, config.DEPOT_LNG),
            cap_l, cap_kg, time_mat, dist_mat,
            time_limit_s=time_limit,
            use_time_windows=use_tw,
            use_pickup_delivery=use_pd,
        )

    if sol.status == "INFEASIBLE":
        st.error(f"Sin solución factible: {sol.raw_solver_output}")
        return

    with st.spinner("Empaquetando bahías y perfil temporal…"):
        try:
            load = packer.pack_truck(sol.ordered_stops, truck_type=truck)
        except Exception as exc:                            # noqa: BLE001
            st.warning(f"Packer falló: {exc}")
            load = None
        profile = rl.temporal_volume_profile(sol.ordered_stops, cap_l)
        kpi_ret = rl.returns_kpi(profile, sol.ordered_stops)

    # --------------------- pestañas de salida ---------------------
    tab_map, tab_3d, tab_table, tab_cmp, tab_explain = st.tabs(
        ["🗺️ Mapa", "📦 Camión 3D", "📋 Tabla", "📊 Comparación", "💬 Explicación"]
    )

    with tab_map:
        m = _build_route_map(stops, sol.ordered_stops,
                              (config.DEPOT_LAT, config.DEPOT_LNG))
        st_folium(m, height=600, width=None, returned_objects=[])

    with tab_3d:
        if load is None:
            st.info("No se pudo construir el packing.")
        else:
            fig = packer.to_3d_visualization(load)
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                f"Coherencia cliente = {load.coherencia_cliente:.2f} · "
                f"ocupación {100 * load.vol_total_l / cap_l:.0f}% del camión {truck}"
            )
        st.markdown("**Perfil temporal de ocupación + retornos:**")
        st.plotly_chart(rl.plot_temporal_profile(profile),
                        use_container_width=True)

    with tab_table:
        arrivals = sol.raw_solver_output.get("arrivals_s", [])
        rows = []
        for k, s in enumerate(sol.ordered_stops, 1):
            arr = arrivals[k - 1] if k - 1 < len(arrivals) else None
            rows.append({
                "#": k,
                "cliente_id": s.cliente_id,
                "cliente": s.cliente_nombre,
                "población": s.poblacion,
                "vol (L)": round(s.volumen_l, 1),
                "peso (kg)": round(s.peso_kg, 1),
                "retornable (L)": round(s.volumen_retornable_l, 1),
                "llegada": _format_hms(arr) if arr is not None else "—",
                "ventana": (f"{_format_hms(s.ventana_inicio)} – "
                            f"{_format_hms(s.ventana_fin)}"
                            if s.ventana_inicio is not None else "—"),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True,
                     hide_index=True)

    with tab_cmp:
        delta_d_pct = 100 * (sol.total_distance_m - base_d) / base_d if base_d else 0.0
        delta_t_pct = 100 * (sol.total_time_s - base_t) / base_t if base_t else 0.0
        cmp_df = pd.DataFrame([
            {"Métrica": "Tiempo total",
             "Real": _format_hms(base_t),
             "Optimizado": _format_hms(sol.total_time_s),
             "Δ": f"{delta_t_pct:+.2f} %"},
            {"Métrica": "Distancia total",
             "Real": f"{base_d/1000:.2f} km",
             "Optimizado": f"{sol.total_distance_m/1000:.2f} km",
             "Δ": f"{delta_d_pct:+.2f} %"},
            {"Métrica": "% retornables recogidos",
             "Real": "75 %",
             "Optimizado": f"{100*kpi_ret.pct_recogido:.1f} %",
             "Δ": f"{(100*kpi_ret.pct_recogido) - 75:+.1f} pp"},
            {"Métrica": "Estado solver",
             "Real": "—",
             "Optimizado": sol.status, "Δ": ""},
        ])
        st.dataframe(cmp_df, hide_index=True, use_container_width=True)

        if kpi_ret.paradas_capacity_violation:
            st.warning(
                f"{kpi_ret.paradas_capacity_violation} parada(s) violan capacidad. "
                "Activa pickup-delivery para forzar restricción dura."
            )

    with tab_explain:
        st.info(
            "La generación automática de explicaciones (Groq Llama 3.3) "
            "se entrega en MVP 10. Por ahora un resumen de fallback:"
        )
        st.markdown(_explanation_fallback(sol, load, kpi_ret, base_t, base_d, truck))


def _explanation_fallback(sol, load, kpi_ret, base_t, base_d, truck) -> str:
    n = len(sol.ordered_stops)
    delta_d = 100 * (sol.total_distance_m - base_d) / base_d if base_d else 0.0
    delta_t = 100 * (sol.total_time_s - base_t) / base_t if base_t else 0.0
    coh = load.coherencia_cliente if load else None
    return (
        f"- Ruta de **{n} paradas** en camión {truck}, "
        f"resuelta con OR-Tools (PATH_CHEAPEST_ARC + Guided Local Search).\n"
        f"- Tiempo total **{delta_t:+.1f}%** vs orden real, "
        f"distancia **{delta_d:+.1f}%**.\n"
        f"- Retornos recogidos: **{100*kpi_ret.pct_recogido:.0f}%** del esperado.\n"
        + (f"- Coherencia de carga por cliente: **{coh:.2f}** — los clientes con "
           f"varias entregas quedan en bahías contiguas.\n" if coh is not None else "")
        + "- El packing respeta el orden inverso de descarga (cliente 1 → bahía 0)."
    )


# ===========================================================================
# Página 3 — Explorar datos
# ===========================================================================

def _page_explorar():
    st.title("🔎 Explorar datos")
    canonical = load_canonical()

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Volumen por transporte")
        per_t = (canonical.groupby("transporte")["volumen_total_l"].sum()
                 .reset_index())
        fig = px.histogram(per_t, x="volumen_total_l", nbins=40,
                            labels={"volumen_total_l": "Volumen total (L)"})
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        st.subheader("Paradas por transporte")
        n_t = (canonical.groupby("transporte").size()
               .reset_index(name="paradas"))
        fig = px.histogram(n_t, x="paradas", nbins=30)
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("% retornable por entrega")
    fig = px.histogram(canonical, x="pct_retornable", nbins=30)
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Top 15 poblaciones por nº de paradas")
    top = (canonical.groupby("poblacion").size().sort_values(ascending=False)
           .head(15).reset_index(name="paradas"))
    fig = px.bar(top, x="poblacion", y="paradas")
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Volumen entregado por día")
    per_day = (canonical.groupby(canonical["fecha"].dt.date)["volumen_total_l"]
               .sum().reset_index().rename(columns={"fecha": "día"}))
    fig = px.line(per_day, x="día", y="volumen_total_l")
    st.plotly_chart(fig, use_container_width=True)


# ===========================================================================
# Router
# ===========================================================================

if page == "Inicio":
    _page_inicio()
elif page == "Optimizar reparto":
    _page_optimizar()
else:
    _page_explorar()
