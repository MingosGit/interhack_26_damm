# Explicabilidad con LLM - Guía de implementación rápida

## 📋 Resumen

Se implementó el módulo `explain.py` que genera **explicaciones en lenguaje natural** de las decisiones de optimización usando **Groq API + Llama 3.1 70B**.

### Archivos creados/actualizados

1. **`src/explain.py`** (660 líneas)
   - Cliente Groq con fallback
   - Explicación de rutas simples
   - Explicación de flotas multi-camión
   - Explicación de empaquetamiento
   - Soporte multiidioma (ES/EN)

2. **`examples/explain_demo.py`**
   - Ejemplos de uso del módulo
   - Instrucciones de setup completas

3. **`EXPLAINABILITY.md`**
   - Documentación técnica completa
   - API reference
   - Troubleshooting

4. **`requirements.txt`** (actualizado)
   - Agregado: `groq>=0.4.1`

5. **`src/vrp_solver.py`** (actualizado)
   - Importa módulo explain
   - Flags CLI: `--explain`, `--explain-lang`
   - Retorna soluciones en run_for_transporte y run_for_fleet

---

## 🚀 Uso rápido

### Setup (5 min)

```bash
# 1. Instalar groq
pip install groq

# 2. Obtener API key gratis
# → Ve a https://console.groq.com
# → Copia la key (empieza con gsk_...)

# 3. Configurar variable de entorno
export GROQ_API_KEY='gsk_...'
```

### CLI - Pedir explicación

```bash
# Single truck + explicación
python3 -m src.vrp_solver --transport 11561535 --explain

# Multi-camión + explicación
python3 -m src.vrp_solver --transport 11561535 --fleet 3 --explain

# En inglés
python3 -m src.vrp_solver --transport 11561535 --explain --explain-lang en

# Con logística inversa + explicación
python3 -m src.vrp_solver --transport 11561535 --reverse-logistics --explain
```

### Programático - Explicación de ruta

```python
from src.explain import explain_solution
from src.vrp_solver import build_stops_from_transporte, solve_single_truck
from src import config, distance_matrix
import pandas as pd

# Cargar datos, construir paradas, resolver...
canonical = pd.read_parquet(config.CANONICAL_PARQUET)
geo = pd.read_parquet(config.GEOCODING_PARQUET)
stops = build_stops_from_transporte(11561535, canonical, geo)

coords = [(config.DEPOT_LAT, config.DEPOT_LNG)] + [(s.lat, s.lng) for s in stops]
time_mat, dist_mat = distance_matrix.get_matrix(coords)

sol = solve_single_truck(stops, (config.DEPOT_LAT, config.DEPOT_LNG),
                         1000, 500, time_mat, dist_mat)

# ← AQUÍ: Generar explicación
explanations = explain_solution(sol, stops, aspect="all", language="es")

print("Explicación de ruta:")
print(explanations["route"])
print("\nExplicación de empaquetamiento:")
print(explanations["packaging"])
```

### Programático - Explicación de flota

```python
from src.explain import explain_fleet_solution
from src.vrp_solver import solve_fleet

fleet_sol = solve_fleet(stops, depot, n_vehicles=3,
                       truck_capacity_l=1000, truck_capacity_kg=500,
                       time_matrix_s=time_mat, dist_matrix_m=dist_mat)

explanations = explain_fleet_solution(fleet_sol, stops, language="es")

print("Explicación de flota:")
print(explanations["fleet"])
```

---

## 🎯 Funciones principales

### `explain_solution(solution, stops, aspect="all", language="es")`
Explica una ruta de 1 camión

```python
explanations = explain_solution(sol, stops, aspect="all", language="es")
# Retorna: {"route": "...", "packaging": "..."}

# Solo ruta
explanations = explain_solution(sol, stops, aspect="route", language="es")

# Solo empaquetamiento
explanations = explain_solution(sol, stops, aspect="packaging", language="es")
```

### `explain_fleet_solution(solution, stops, language="es")`
Explica una distribución multi-camión

```python
explanations = explain_fleet_solution(fleet_sol, stops, language="es")
# Retorna: {"fleet": "...", "routes": [...]}
```

### `explain_route()`, `explain_packaging()`, `explain_fleet()`
Funciones de bajo nivel disponibles

---

## 📊 Ejemplos de output

### Explicación de ruta

```
Se optimizó la ruta priorizando minimizar la distancia total (12% menos vs baseline).
El orden propuesto (Mollet → Terrassa → Sabadell → Barcelona → Hospitalet)
sigue un patrón geográfico coherente que evita cruzamientos. Se respetaron 
las capacidades de volumen (1000L) y peso (500kg), con aprovechamiento del 87%.
```

### Explicación de flota

```
Los 25 clientes se distribuyeron entre 3 camiones agrupando por proximidad geográfica.
Camión 1 cubre zona norte (11 paradas, 980L), camión 2 zona centro (8 paradas, 850L),
camión 3 zona sur (6 paradas, 650L). La asignación minimiza tiempo de tránsito
entre paradas (distancia total 127km vs 156km si fuera 1 camión).
```

### Explicación de empaquetamiento

```
La carga inicial es 945L (producto a entregar). Sigue estrategia FIFO:
se descargan paradas progresivamente, pero se llena con retornables
(palés vacíos) para mantener densidad. El pico máximo de carga viva
ocurre en parada 12 (Plaça Catalunya) con 1020L. Los retornables
suman 250L totales (26% del volumen original).
```

---

## ⚙️ Configuración avanzada

### Cambiar modelo de LLM

En `src/explain.py`, línea ~130:

```python
# De:
model="llama-3.1-70b-versatile",

# A (opciones):
model="llama-3.1-8b-instant",      # Más rápido, menos contexto
model="mixtral-8x7b-32768",        # Alta capacidad
```

### Límite de tokens

```python
explanations = explain_route(sol, stops, max_tokens=300, language="es")
```

### Fallback sin LLM (siempre disponible)

Si no configuras GROQ_API_KEY, el módulo retorna explicaciones básicas:
```
✓ Ruta optimizada:
  - 12 paradas
  - Distancia: 45.32 km
  - Tiempo: 6h23m45s
  - Criterios: minimizar distancia + tiempo, respetar capacidad
```

---

## 🔍 Troubleshooting

### "GROQ_API_KEY no configurada"

```bash
export GROQ_API_KEY='gsk_...'
echo $GROQ_API_KEY  # Verifica que está set
```

### "groq not installed"

```bash
pip install groq>=0.4.1
```

### "Connection timeout"

→ Groq API está lento, intenta con menos tokens:
```python
explain_route(sol, stops, max_tokens=300)
```

### "Modelo no disponible"

Usa siempre `llama-3.1-70b-versatile` (disponible gratis)

---

## 📈 Performance

| Operación | Tiempo | Tokens |
|-----------|--------|--------|
| Explicar ruta simple | 3-5s | 400 |
| Explicar empaquetamiento | 2-4s | 350 |
| Explicar flota | 5-8s | 600 |

Tiempos aproximados en red real (incluye latencia)

---

## 📚 Documentación

Para más detalles: [`EXPLAINABILITY.md`](EXPLAINABILITY.md)

Para ejemplos completos: [`examples/explain_demo.py`](examples/explain_demo.py)

---

## ✅ Checklist de verificación

- [ ] Instalar: `pip install groq`
- [ ] API key: https://console.groq.com
- [ ] Configurar: `export GROQ_API_KEY='...'`
- [ ] Probar CLI: `python3 -m src.vrp_solver --transport 11561535 --explain`
- [ ] Probar programático: `python3 examples/explain_demo.py`

---

## 🚀 Próximos pasos

- [ ] Caché de explicaciones
- [ ] Dashboard con explicaciones (Streamlit)
- [ ] Exportar a PDF/reportes
- [ ] Más idiomas (FR, DE, PT)
- [ ] Explicaciones personalizadas por rol (admin/driver/client)
