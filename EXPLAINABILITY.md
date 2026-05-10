# Módulo de Explicabilidad con LLM (explain.py)

## Descripción

El módulo `explain.py` genera **explicaciones en lenguaje natural** de las decisiones del solver VRP usando **Groq API** con **Llama 3.1 70B**.

### ¿Qué explica?

✅ **Rutas simples** (1 camión)
- "Por qué se eligió esta orden de paradas?"
- "Qué criterios guiaron la optimización?"

✅ **Rutas multi-camión** (flota CVRP)
- "¿Por qué se distribuyó así entre camiones?"
- "¿Qué equilibrio existe entre utilización de flota y calidad de ruta?"

✅ **Empaquetamiento y carga**
- "¿Cuál fue la estrategia de carga (LIFO/FIFO/mixta)?"
- "¿Por qué se alcanzó el pico de carga en la parada X?"
- Perfil ASCII de carga a lo largo de la ruta

---

## Setup

### 1. Instalar dependencias

```bash
# Agregar groq a requirements
pip install groq>=0.4.1

# O reinstalar todo:
pip install -r requirements.txt
```

### 2. Obtener API Key de Groq

Groq ofrece API **gratis** con límite generoso (incluidas pruebas):

1. Ve a: https://console.groq.com
2. Regístrate (es rápido)
3. Genera una nueva API key
4. Cópiala (empieza con `gsk_...`)

### 3. Configurar variable de entorno

```bash
# Temporal (solo esta sesión)
export GROQ_API_KEY='gsk_...'

# Permanente (agregar a ~/.bashrc o ~/.zshrc)
echo "export GROQ_API_KEY='gsk_...'" >> ~/.bashrc
source ~/.bashrc
```

### 4. Verificar que funciona

```bash
python3 -c "from groq import Groq; print('✓ Groq cargado correctamente')"
```

---

## Uso

### API 1: Explicar ruta simple (1 camión)

```python
from src.explain import explain_solution, explain_route
from src.vrp_solver import build_stops_from_transporte, solve_single_truck
from src import config, distance_matrix
import pandas as pd

# Cargar datos
canonical = pd.read_parquet(config.CANONICAL_PARQUET)
geo = pd.read_parquet(config.GEOCODING_PARQUET)

# Construir paradas
stops = build_stops_from_transporte(11561535, canonical, geo)

# Calcular matrices y resolver
coords = [(config.DEPOT_LAT, config.DEPOT_LNG)] + [(s.lat, s.lng) for s in stops]
time_mat, dist_mat = distance_matrix.get_matrix(coords)

cap_l, cap_kg = 1000, 500
sol = solve_single_truck(stops, (config.DEPOT_LAT, config.DEPOT_LNG),
                         cap_l, cap_kg, time_mat, dist_mat)

# Generar explicaciones
explanations = explain_solution(sol, stops, aspect="all", language="es")

print("📍 Explicación de la ruta:")
print(explanations["route"])
print("\n📦 Explicación de empaquetamiento:")
print(explanations["packaging"])
```

**Output ejemplo**:
```
📍 Explicación de la ruta:
Se optimizó la ruta priorizando la reducción de distancia (15% menos vs baseline). 
El orden propuesto (Mollet → Terrassa → Sabadell → Barcelona) minimiza 
cruzamientos y sigue un patrón geográfico coherente. Se respetaron las capacidades 
de volumen (1000L) y peso (500kg), con aprovechamiento del 87%.

📦 Explicación de empaquetamiento:
La carga aumenta progresivamente en las primeras 8 paradas (FIFO parcial), 
alcanzando pico de 945L en Barcelona. Luego desciende debido a retornables 
(palés vacíos). El perfil minimiza riesgo de sobrecarga manteniendo 
capacidad libre ≥ 20L en todo momento.
```

### API 2: Explicar flota multi-camión

```python
from src.explain import explain_fleet_solution
from src.vrp_solver import solve_fleet

# Resolver con 3 camiones
fleet_sol = solve_fleet(stops, (config.DEPOT_LAT, config.DEPOT_LNG),
                        n_vehicles=3,
                        truck_capacity_l=cap_l, truck_capacity_kg=cap_kg,
                        time_matrix_s=time_mat, dist_matrix_m=dist_mat)

# Generar explicación
explanations = explain_fleet_solution(fleet_sol, stops, language="es")

print("🚚 Explicación de distribución de flota:")
print(explanations["fleet"])

print("\nRutas por camión:")
for route_info in explanations["routes"]:
    print(f"  {route_info}")
```

**Output ejemplo**:
```
🚚 Explicación de distribución de flota:
Se distribuyeron 25 clientes entre 3 camiones optimizando geografía y carga. 
Camión 1 cubre zona norte (11 paradas, 980L), camión 2 zona centro (8 paradas, 850L), 
camión 3 zona sur (6 paradas, 650L). La asignación respeta concentración geográfica 
para minimizar tiempo de tránsito entre paradas. Distancia total: 127km 
(vs 156km si fuera 1 camión).
```

### API 3: Explicaciones individuales detalladas

```python
from src.explain import explain_route, explain_packaging, explain_fleet

# Solo explicar la ruta
route_explanation = explain_route(sol, stops, language="es")
print(route_explanation)

# Solo explicar empaquetamiento
packaging_explanation = explain_packaging(sol, language="es")
print(packaging_explanation)

# Solo explicar flota
fleet_explanation = explain_fleet(fleet_sol, stops, language="es")
print(fleet_explanation)
```

---

## Parámetros

### `explain_solution(solution, stops, aspect="all", language="es")`

| Parámetro | Tipo | Descripción | Valores |
|-----------|------|-------------|---------|
| `solution` | `Solution` | Solución del solver | Retornado por `solve_single_truck()` |
| `stops` | `list[Stop]` | Paradas originales | Lista antes de optimizar |
| `aspect` | `str` | Qué explicar | `"route"`, `"packaging"`, `"all"` |
| `language` | `str` | Idioma | `"es"` (español), `"en"` (inglés) |

**Retorna**: `dict[str, str]` con claves `"route"` y/o `"packaging"`

### `explain_fleet_solution(solution, stops, language="es")`

| Parámetro | Tipo | Descripción |
|-----------|------|-------------|
| `solution` | `FleetSolution` | Solución multi-camión |
| `stops` | `list[Stop]` | Paradas originales |
| `language` | `str` | `"es"` o `"en"` |

**Retorna**: `dict[str, str]` con claves `"fleet"` y `"routes"`

### `explain_route()`, `explain_packaging()`, `explain_fleet()`

Funciones de bajo nivel. Parámetros adicionales:
- `max_tokens`: Límite de tokens en respuesta (default 500-800)

---

## Modelos disponibles

Groq soporta múltiples modelos. El código usa **Llama 3.1 70B** por defecto:

```python
message = client.messages.create(
    model="llama-3.1-70b-versatile",  # ← Puedes cambiar aquí
    max_tokens=500,
    messages=[...]
)
```

**Otros modelos disponibles**:
- `llama-3.1-8b-instant` (más rápido, menos contexto)
- `llama-3.1-70b-versatile` (recomendado, best quality/speed)
- `mixtral-8x7b-32768` (alta capacidad)

---

## Fallback (sin Groq)

Si no encuentras API key o groq no está instalado, el módulo retorna explicaciones **básicas** en texto plano:

```
✓ Ruta optimizada:
  - 12 paradas
  - Distancia: 45.32 km
  - Tiempo: 6h23m45s
  - Criterios: minimizar distancia + tiempo, respetar capacidad
```

---

## Ejemplos end-to-end

Ve a `examples/explain_demo.py`:

```bash
python3 examples/explain_demo.py
```

Muestra ejemplos de código para:
1. Explicar ruta de 1 camión
2. Explicar distribución multi-camión
3. Pasos completos de setup

---

## Performance

| Operación | Tiempo típico | Tokens |
|-----------|---------------|--------|
| Explicar ruta | 2-5s | ~400 |
| Explicar packagingaging | 2-5s | ~350 |
| Explicar flota | 3-7s | ~600 |

Tiempos en API Groq (excluye latencia de red).

---

## Limitaciones y notas

### Limitaciones

1. **Requiere API key de Groq** (gratis, pero requiere registro)
2. **Latencia** de red: 2-7s por explicación
3. **No determinista**: Cada llamada puede generar texto ligeramente diferente
4. **Contexto limitado**: Si hay >30 paradas, se puede truncar la lista en el prompt

### Seguridad

- La API key se lee desde variable de entorno, **no está hardcodeada**
- El módulo logguea warnings si no está configurada
- Se puede desactivar con: `GROQ_AVAILABLE = False`

### Multiidioma

Soporta explicaciones en:
- ✅ Español (`language="es"`)
- ✅ Inglés (`language="en"`)

Fácil agregar más: Solo cambiar el prompt en la función.

---

## Debugging

### "GROQ_API_KEY no configurada"

Haz:
```bash
export GROQ_API_KEY='tu_clave_aqui'
python3 tu_script.py
```

### "groq not installed"

```bash
pip install groq
```

### "Connection timeout"

Groq API puede estar lento. Intenta:
```python
explanation = explain_route(sol, stops, language="es", max_tokens=300)
```

O usa fallback (sin LLM):
```python
# Automático: si falla Groq, retorna fallback
```

---

## Roadmap (futuro)

- [ ] Caché de explicaciones para evitar llamadas duplicadas
- [ ] Explicaciones personalizadas por rol (admin, driver, client)
- [ ] Integración con dashboards (Streamlit/Plotly)
- [ ] Explicaciones de por qué falló (INFEASIBLE)
- [ ] Soporte para más idiomas (FR, DE, PT)
- [ ] Exportar explicaciones a PDF/reportes

---

## Referencias

- **Groq**: https://groq.com
- **Llama 3.1**: https://www.meta.com/en/news/llama-3-1/
- **API Docs**: https://console.groq.com/docs
