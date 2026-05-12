# Damm Smart Truck

> Optimización conjunta de **ruta** y **carga** de camiones de reparto para Damm Distribución (DDI Mollet).
> Proyecto desarrollado para el reto Damm de la **InterHack BCN 2026**.

Combina un solver VRP (OR-Tools) con un packer adaptado a las restricciones físicas reales del camión (lonas laterales, bahías longitudinales, toldos para retornables) y una capa de explicabilidad en lenguaje natural con LLM.

---

## ¿Qué hace?

- **Optimiza rutas** sobre la red de carreteras real (matriz OSRM) con ventanas horarias por cliente y prioridades configurables.
- **Distribuye la carga** entre las bahías del camión respetando el acceso lateral, el apilamiento por estabilidad y la capacidad por palet.
- **Modela logística inversa** como un balance volumétrico temporal: a medida que se entrega, el toldo lateral se rellena con envases vacíos.
- **Elige la mezcla de flota** automáticamente entre los camiones disponibles (11×6P, 4×8P, 1×FUR), o permite seleccionarla a mano.
- **Recomienda layout del almacén** moviendo los SKUs más frecuentes al muelle según uso histórico.
- **Explica cada decisión** con Groq + Llama 3.3 (criterios priorizados, clusters de clientes, fricciones, recomendaciones accionables).

---

## Quick start

Requiere Python 3.11+.

```bash
# Clonar e instalar
git clone https://github.com/MingosGit/interhack_26_damm
cd interhack_26_damm
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# (Opcional) Activar explicabilidad LLM
echo "GROQ_API_KEY=tu_clave_de_groq" > .env

# Lanzar el dashboard
streamlit run app/dashboard.py
```

El dashboard se abre en `http://localhost:8501`. Por defecto trae el transporte `11561535` cacheado.

---

## Cómo se usa

1. **Sidebar** → elegir transporte, tipo de camión y modo:
   - *Un solo camión*: una ruta con el tipo seleccionado.
   - *Flota múltiple*: N camiones iguales (manual).
   - *Flota óptima*: el sistema decide la mejor combinación dentro del inventario disponible.
2. **RESOLVER OPTIMIZACIÓN** → corre el solver y rellena las 6 pestañas:
   - 📊 **Métricas** — ahorro vs. baseline real (km, tiempo, %) + composición de flota.
   - 🗺️ **Ruta** — mapa Folium con paradas color-coded por vehículo.
   - 📦 **Carga** — esquema del camión (bahías N/S + toldos), orden de picking LIFO, detalle por zona, panel híbrido referencia/cliente.
   - 💡 **Insights** — criterios priorizados, clusters geográficos, fricciones, recomendaciones, layout del almacén.
   - 🧠 **Explicaciones** — narrativa en lenguaje natural (Groq Llama 3.3).
   - 📄 **Técnicas** — JSON crudo del solver.

---

## Arquitectura

```
data/                         # Inputs originales (xlsx)
  Hackaton.xlsx               # Entregas, cabeceras, clientes, zonas, materiales
  ZM040.XLSX                  # Maestro de materiales (volumen/peso por UMA)
  Horarios Entrega.XLSX       # Ventanas horarias por cliente
  Layout Mollet.xlsx          # Layout físico del almacén

cache/                        # Pre-procesado (parquet) y outputs HTML
  canonical.parquet           # Tabla canónica unificada
  geocoding.parquet           # Lat/lng de cada cliente
  distance_matrix.parquet     # Matriz de tiempos/distancias

src/
  config.py                   # Constantes (flota, depot, retornables)
  etl.py                      # Limpieza + canonicalización
  geocoding.py                # Nominatim + caché
  distance_matrix.py          # OSRM + caché
  horarios.py                 # Parsing de ventanas horarias
  routing.py                  # Cliente OSRM para geometrías de ruta
  vrp_solver.py               # OR-Tools CVRP (single + flota + mezcla óptima)
  packer.py                   # Packer por bahías (heurístico)
  loading_visualization.py    # Render del camión (4 bahías + toldos)
  insights.py                 # Análisis automático y recomendaciones
  warehouse.py                # Recomendación de layout por frecuencia de SKU
  explain.py                  # Cliente Groq para explicabilidad LLM
  exceptions.py

app/
  dashboard.py                # UI Streamlit completa
```

---

## Stack

| Capa | Herramienta | Por qué |
|---|---|---|
| Optimización | **Google OR-Tools** | CVRP, VRPTW y heterogéneo en una sola librería |
| Datos | **pandas + pyarrow** | Parquet rápido + datasets de 83k filas sin tirar de Spark |
| Geocoding | **Nominatim (OSM)** | Gratis, sin clave, cacheado |
| Matriz dist/tiempo | **OSRM público** | Endpoint `/table` con tiempos reales por carretera |
| Mapa | **Folium** | Integración nativa con Streamlit |
| 3D / Charts | **Plotly** | Visualización 3D del camión y perfiles de carga |
| Dashboard | **Streamlit** | Demo desplegable en una tarde |
| LLM | **Groq · Llama 3.3 70B** | Free tier, latencia <1 s, explicabilidad en producción |

---

## Decisiones de diseño

Las tres ideas-fuerza del proyecto:

1. **Acceso lateral cambia todo el packing.** Los camiones de DDI tienen lonas laterales, así que no aplica el LIFO clásico. Modelamos el camión como una secuencia de bahías longitudinales (palets) con toldos laterales para envases vacíos. La mayoría de soluciones genéricas de bin-packing 3D atacan el problema equivocado.
2. **La logística inversa es un balance volumétrico temporal**, no un VRP estándar de pickup-delivery. A medida que se entrega, el camión libera espacio que se rellena con retornables. El modelo incorpora esa dinámica.
3. **Híbrido referencia/cliente.** Ni "todo por cliente" ni "todo por referencia": clusterizar por barrio → asignar bahía por clúster → dentro de la bahía, ordenar por cliente → dentro del cliente, por SKU y estabilidad (barriles abajo, cajas arriba).

---

## Datos

El dataset original (no incluido en el repo) cubre **2 meses de operación real**: 82.849 líneas de entrega, 889 transportes, 1.369 clientes únicos. Para un transporte típico (~25 paradas, 6.000 L) el solver responde en 2–5 s.

Toda la cadena de datos está cacheada en `cache/*.parquet`, así que tras la primera ETL las ejecuciones son inmediatas.

---

## Configuración

Variables de entorno (`.env`):

```bash
GROQ_API_KEY=tu_clave         # Opcional, para explicabilidad LLM
GROQ_MODEL=llama-3.3-70b-versatile  # Opcional, modelo a usar
```

Sin `GROQ_API_KEY` el sistema cae a explicaciones de respaldo (sin LLM) y el dashboard lo indica con un warning.

---

## Estado del proyecto

Prototipo de hackathon. Funcional end-to-end pero no production-ready: faltan tests de integración completos, observabilidad, autenticación y un pipeline de re-ETL automatizado.

---

