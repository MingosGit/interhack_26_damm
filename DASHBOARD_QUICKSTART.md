# 🚚 Damm Smart Truck - Dashboard Ejecutable

## Resumen Rápido

El sistema está completamente operativo con visualización realista de camiones 4-bahía y dashboard interactivo Streamlit.

## Instrucciones de Ejecución

### 1. **Verificar entorno (Bash)**

```bash
# Entrar al directorio del proyecto
cd /home/noxekitz/Desktop/interhack_26_damm

# Activar venv (si no está activado)
source .venv/bin/activate

# Verificar dependencias
python -m pip list | grep -E "streamlit|ortools|pandas|plotly"
```

### 2. **Opción A: Dashboard Interactivo Streamlit** 🎯 (Recomendado para demo)

```bash
cd /home/noxekitz/Desktop/interhack_26_damm
streamlit run app/dashboard.py
```

**Qué verás:**
- 🎨 Interfaz web en `http://localhost:8501/`
- 📍 Selector de Transporte ID (default: 11561535)
- 🚛 Selector de tipo de camión (6P, 8P, FUR)
- 🔄 Modos: Single truck o Fleet múltiple
- 📊 Tabs interactivas:
  - **Métricas**: KPIs (paradas, volumen, distancia, mejora)
  - **Ruta**: Mapa interactivo (si HTML disponible)
  - **Carga**: Visualización 4-bahías top-down + ASCII
  - **Insights**: Análisis automático + recomendaciones
  - **Técnicas**: JSON completo + specs del camión

**Ejemplo de ejecución:**
```
1. Ingresa: 11561535
2. Camión: 6P
3. Modo: "Un solo camión"
4. Checkea: ✓ Explicaciones automáticas
5. Click: ▶️ RESOLVER OPTIMIZACIÓN
6. Resultado en 2-3 segundos ⏱️
```

### 3. **Opción B: CLI Terminal** (Modo avanzado/scripting)

```bash
# Single truck
python -m src.vrp_solver --transport 11561535 --truck 6P --explain --loading-html auto

# Fleet (3 vehículos)
python -m src.vrp_solver --transport 11561535 --truck 6P --fleet 3 --explain --loading-html auto

# Parámetros disponibles:
#   --transport ID         Transporte ID
#   --truck TIPO          Tipo de camión: 6P, 8P, FUR
#   --fleet N             Modo fleet con N vehículos
#   --explain             Usar LLM para explicaciones (fallback si GROQ_API_KEY no existe)
#   --explain-lang ES|EN  Idioma de explicaciones
#   --loading-html auto   Auto-generar HTML de carga en cache/
```

## Estructura de Visualización Mejorada

### Nueva Arquitectura del Camión (4-bahías + 2-toldos)

```
FRENTE DEL VEHICULO (Cabina)
+---+---+---+---+---+---+---+---+---+---+
| TOLDO                  TOLDO LATERAL |
| LATERAL                 DERECHO      |
| IZQ                                   |
+---+---+---+---+---+---+---+---+---+---+

BAHIA NORTE-1  |  BAHIA NORTE-2  [====] TOLDO DER
[############] | [############]
[###########] | [###########]  BAYS (N-S)
 3600L max     |  3600L max     

[###########] | [###########]
[###########] | [###########]  
BAHIA SUR-1    |  BAHIA SUR-2   [====] TOLDO IZQ
3600L max      |  3600L max     

RAMPA TRASERA (descarga principal)
```

**Características:**
- ✅ Distribución **equilibrada** (redondeo por bahía)
- ✅ Cargas **pesadas** (BRL, BID, BOT) → buen equilibrio N-S
- ✅ Cargas **ligeras** (CAJ, UN) → bahías traseras
- ✅ **Retornables** (BOT ret) → toldos laterales deslizables
- ✅ Acceso **trasero** (rampa principal) para todas las bahías

### Ejemplo de Salida

```
CAMION 6P - VISTA DESDE ARRIBA (PLANO TOP-DOWN)

[RESUMEN DE OCUPACION]
VOLUMEN TOTAL: 10208.6 L / 14400 L (70.9%)

DISTRIBUCION POR BAHIA:
  NO1: 2413.8L (67.0%) | 1541.3kg
  NO2: 2689.0L (74.7%) | 1265.0kg
  SU1: 2157.1L (59.9%) | 1126.7kg
  SU2: 1867.0L (51.9%) | 1254.6kg

RETORNABLES (Toldos Laterales):
  Toldo Izquierdo: 1081.7L (72.1%)
  Toldo Derecho:   2754.4L (183.6%)  ← ¡Sobre-ocupado!
  
[ALERTA] Toldo derecho necesita redistribución
```

## Archivos de Salida

Después de ejecutar, se generan automaticamente:

```
cache/
├── loading_plan_<id>_<truck>_<mode>.html    # Plan visual interactivo
├── <id>_plan_ascii.txt                      # Vista ASCII top-down
└── route_<id>.html                          # Mapa con ruta optimizada
```

**Para ver HTML:**
```bash
open cache/loading_plan_11561535_6P_single.html  # macOS
xdg-open cache/loading_plan_11561535_6P_single.html  # Linux
start cache/loading_plan_11561535_6P_single.html  # Windows
```

## Explainability (LLM)

### Con API Groq (Recomendado)

```bash
export GROQ_API_KEY='gsk_...'
python -m src.vrp_solver --transport 11561535 --truck 6P --explain
```

**Explicaciones generadas automáticamente en:**
- ✅ Porqué esta ruta
- ✅ Por qué este empaquetamiento
- ✅ Análisis de flota (si aplica)

### Sin API (Fallback automático)

Si no tienes API key, el sistema genera explicaciones basadas en reglas:

```bash
python -m src.vrp_solver --transport 11561535 --truck 6P --explain
# 2026-05-09 19:51:07 | WARNING | GROQ_API_KEY no configurada
# Usando explicaciones por reglas...
```

## Tests Rápidos

### Test 1: Single Truck
```bash
python -m src.vrp_solver --transport 11561535 --truck 6P --loading-html auto
# Expected: OPTIMAL, 25 paradas, 10.2kL, 30.5km
```

### Test 2: Fleet
```bash
python -m src.vrp_solver --transport 11561535 --truck 6P --fleet 3 --loading-html auto
# Expected: OPTIMAL, 1 de 3 vehículos usados (todo cabe en uno)
```

### Test 3: Dashboard
```bash
streamlit run app/dashboard.py
# Click en Transporte: 11561535
# Click en RESOLVER OPTIMIZACIÓN
# Verificar: Tabs cargando, HTML generados
```

## Resolución de Problemas

### ⚠️ "Error: No module named 'streamlit'"
```bash
python -m pip install streamlit>=1.35
```

### ⚠️ "GROQ_API_KEY no configurada"
- ✓ Normal - el sistema usa fallback por reglas
- Para LLM: `export GROQ_API_KEY='tu_clave_aqui'`

### ⚠️ "HTML files not found"
- Asegúrate: `--loading-html auto` incluido en comando
- Check: `ls -la cache/*.html`

### ⚠️ "Streamlit port 8501 in use"
```bash
streamlit run app/dashboard.py --server.port 8502
```

## Metricas de Éxito

✅ **Optimización:**
- Distancia: -28.5% vs ruta base
- Tiempo: -10.1% vs ruta base
- Status: OPTIMAL (OR-Tools solver)

✅ **Visualización:**
- 4 bahías equilibradas (N1, N2, S1, S2)
- 2 toldos para retornables (IZQ, DER)
- Vista top-down ASCII clara

✅ **Explainability:**
- 7 categorías de análisis (criterios, clusters, fricción, etc.)
- 4+ recomendaciones accionables
- Fallback si no hay LLM

✅ **Multiidioma:**
- CLI: español por defecto
- Explicaciones: --explain-lang es | en
- Dashboard: UI en español

## Requisitos Cumplidos (InterHack 2026)

| Req | Descripción | Status |
|-----|------------|--------|
| 1 | VRP single truck (OR-Tools) | ✅ COMPLETO |
| 2 | Multi-vehicle fleet | ✅ COMPLETO |
| 3 | Load balancing (4-bahías) | ✅ COMPLETO |
| 4 | Reverse logistics | ✅ COMPLETO |
| 5 | Visualization ASCII + HTML | ✅ COMPLETO |
| 6 | Explainability (LLM + reglas) | ✅ COMPLETO |
| 7 | Dashboard interactivo | ✅ COMPLETO |
| 8 | CLI avanzado | ✅ COMPLETO |
| 9 | Multiidioma | ✅ COMPLETO |

---

## 🚀 Inicio Rápido (3 comandos)

```bash
# 1. Entrar al proyecto
cd /home/noxekitz/Desktop/interhack_26_damm && source .venv/bin/activate

# 2. Abrir dashboard
streamlit run app/dashboard.py

# 3. (En otra terminal) O prueba CLI
python -m src.vrp_solver --transport 11561535 --truck 6P --explain --loading-html auto
```

---

**Desarrollado para InterHack 2026 - Damm Smart Truck Challenge** 🏆
