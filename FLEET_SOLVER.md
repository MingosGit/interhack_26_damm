# Fleet Solver (CVRP Multi-Vehículo)

## Descripción

Se ha implementado **`solve_fleet()`** para resolver el **Capacitated Vehicle Routing Problem (CVRP)**, permitiendo la optimización automática de rutas para una flota de múltiples camiones.

### Diferencia entre funciones

| Función | Problema | Vehículos | Uso |
|---------|----------|-----------|-----|
| `solve_single_truck()` | VRP clásico | 1 | Optimizar orden de paradas en un único camión |
| `solve_fleet()` | **CVRP** | N | **Repartir paradas entre N camiones y optimizar cada ruta** |

---

## API de `solve_fleet()`

```python
def solve_fleet(
    stops: list[Stop],                      # Paradas a entregar
    depot: tuple[float, float],             # Coordenadas del depósito
    n_vehicles: int,                        # Número de camiones en la flota
    truck_capacity_l: float,                # Capacidad de volumen por camión (litros)
    truck_capacity_kg: float,               # Capacidad de peso por camión (kg)
    time_matrix_s: np.ndarray,              # Matriz de tiempos (n+1) × (n+1), en segundos
    dist_matrix_m: np.ndarray,              # Matriz de distancias (n+1) × (n+1), en metros
    *,
    max_route_time_s: int = 8 * 3600,      # Máximo tiempo por ruta (por defecto 8 h)
    time_limit_s: int = 20,                # Límite de tiempo de resolución (segundos)
    use_time_windows: bool = False,        # Activar ventanas horarias de clientes
    depot_open_s: int = config.JORNADA_INICIO_S,    # Hora apertura depósito
    depot_close_s: int = config.JORNADA_FIN_S,      # Hora cierre depósito
) -> FleetSolution:
```

### Parámetros

- **`stops`**: Lista de clientes (`Stop`) con ubicación, volumen, peso, ventana horaria (opcional).
- **`depot`**: Coordenadas (lat, lng) del depósito central.
- **`n_vehicles`**: Número de camiones disponibles. OR-Tools distribuirá automáticamente los clientes.
- **`truck_capacity_l`** / **`truck_capacity_kg`**: Límites de capacidad por vehículo.
- **`time_matrix_s`** / **`dist_matrix_m`**: Matrices precomputadas (incluyendo la fila/columna 0 para depot).
- **`use_time_windows`**: Si `True`, respeta ventanas de servicio de cada cliente.

### Retorno: `FleetSolution`

```python
@dataclass
class FleetSolution:
    routes: list[list[Stop]]           # Paradas por cada vehículo
    route_metrics: list[dict]          # Tiempo, distancia, etc. por ruta
    total_time_s: int                  # Tiempo total (suma de todas las rutas)
    total_distance_m: int              # Distancia total (suma)
    status: str                        # "OPTIMAL", "FEASIBLE", "INFEASIBLE"
    n_vehicles_used: int               # Cuántos vehículos se usaron realmente
    raw_solver_output: dict             # Información bruta de OR-Tools
```

---

## Ejemplo de uso

### 1. Uso programático

```python
from src.vrp_solver import solve_fleet, build_stops_from_transporte
from src import config, distance_matrix
import pandas as pd

# Cargar datos
canonical = pd.read_parquet(config.CANONICAL_PARQUET)
geo = pd.read_parquet(config.GEOCODING_PARQUET)

# Construir paradas para un transporte
transporte_id = 11561535
stops = build_stops_from_transporte(transporte_id, canonical, geo)

# Calcular matrices de distancia y tiempo
coords = [(config.DEPOT_LAT, config.DEPOT_LNG)] + [(s.lat, s.lng) for s in stops]
time_mat, dist_mat = distance_matrix.get_matrix(coords)

# Resolver con 3 camiones
cap_l, cap_kg = 1000, 500  # Capacidades
sol = solve_fleet(
    stops,
    depot=(config.DEPOT_LAT, config.DEPOT_LNG),
    n_vehicles=3,
    truck_capacity_l=cap_l,
    truck_capacity_kg=cap_kg,
    time_matrix_s=time_mat,
    dist_matrix_m=dist_mat,
    time_limit_s=30,
    use_time_windows=True,
)

# Acceder a resultados
print(f"Status: {sol.status}")
print(f"Vehículos usados: {sol.n_vehicles_used}")
print(f"Tiempo total: {sol.total_time_s}s")
print(f"Distancia total: {sol.total_distance_m/1000:.2f}km")

# Iterar sobre rutas
for v_idx, route in enumerate(sol.routes):
    print(f"\nRuta {v_idx + 1}: {len(route)} paradas")
    for stop in route:
        print(f"  - {stop.cliente_nombre} ({stop.volumen_l}L)")
```

### 2. Uso desde CLI

Se ha actualizado el comando de línea para soportar `--fleet`:

```bash
# Resolver con 1 camión (como antes)
python3 -m src.vrp_solver --transport 11561535 --truck 6P

# Resolver con 3 camiones (NUEVO)
python3 -m src.vrp_solver --transport 11561535 --truck 6P --fleet 3

# Con ventanas horarias y límite de tiempo
python3 -m src.vrp_solver --transport 11561535 --fleet 2 --truck 4P --time-windows --time-limit 30
```

### 3. Función de prueba

Se incluye `run_for_fleet()` para pruebas rápidas:

```python
from src.vrp_solver import run_for_fleet

result = run_for_fleet(
    transporte_id=11561535,
    n_vehicles=2,
    truck="6P",
    time_limit_s=30,
    use_time_windows=True,
)

print(result)
# {
#   'transporte': 11561535,
#   'n_stops': 25,
#   'n_vehicles_requested': 2,
#   'n_vehicles_used': 2,
#   'baseline_time_s': 50000,
#   'opt_time_s': 42000,
#   'delta_time_pct': -16.0,
#   ...
# }
```

---

## Características

### ✅ Implementado

- **Multi-vehículo (CVRP)**: Distribuye automáticamente N clientes entre M camiones
- **Capacidad doble**: Volumen (litros) + Peso (kg) simultáneamente
- **Ventanas horarias**: Respeta horarios de atención de clientes (VRPTW)
- **Optimización metaheurística**: Guided Local Search + PATH_CHEAPEST_ARC
- **Métricas por ruta**: Tiempo, distancia, carga, paradas por vehículo
- **Soporte CLI**: Comando `--fleet N` en la línea de comandos

### 🚀 Diferencias vs `solve_single_truck()`

| Aspecto | single_truck | fleet |
|---------|--------------|-------|
| INFEASIBLE si capacidad insuficiente | ✅ Sí | ✅ Distribuye entre vehículos |
| Optimiza tiempo total | ✅ Sí | ✅ Sí (todas las rutas) |
| Maneja múltiples rutas | ❌ No | ✅ Sí |
| Retorna paradas por vehículo | ❌ Una lista | ✅ Multiple rutas |

---

## Algoritmo

`solve_fleet()` usa el mismo motor que `solve_single_truck()`:

1. **Preproceso**: Valida capacidades, ventanas, matrices
2. **Creación del modelo**: `RoutingIndexManager(n_nodes, n_vehicles, depot)`
3. **Dimensiones**:
   - **Tiempo**: Servicio en origen + tránsito
   - **Volumen**: Suma de demandas ≤ capacidad por vehículo
   - **Peso**: Suma de pesos ≤ capacidad por vehículo
4. **Búsqueda**: `PATH_CHEAPEST_ARC` inicial + `GUIDED_LOCAL_SEARCH`
5. **Extracción**: Itera por cada vehículo extrayendo su ruta

---

## Validaciones

`solve_fleet()` garantiza:

- ✅ **Cobertura completa**: Todas las paradas están asignadas (a menos que INFEASIBLE)
- ✅ **Respeto a capacidades**: Cada ruta respeta volumen y peso máximo
- ✅ **Respeto a ventanas**: Si `use_time_windows=True`, llega dentro de horarios
- ✅ **Retorno al depósito**: Todas las rutas terminan en depot
- ✅ **Número de vehículos**: Usa ≤ `n_vehicles` vehículos (puede usar menos)

---

## Casos de uso

### Caso 1: Capacidad insuficiente en 1 camión

```python
# Sin multi-vehículo: INFEASIBLE
sol_single = solve_single_truck(..., truck_capacity_l=500)
# Status: INFEASIBLE (paradas suman 2000L)

# Con multi-vehículo: SOLUCIONADO
sol_fleet = solve_fleet(..., n_vehicles=4, truck_capacity_l=500)
# Status: OPTIMAL, usa 4 camiones
```

### Caso 2: Optimizar tiempo total en zona

```python
# Zona con 30 paradas
# ¿Cuántos camiones necesito?

sol_1 = solve_fleet(stops, n_vehicles=1, ...)  # Puede ser INFEASIBLE
sol_2 = solve_fleet(stops, n_vehicles=2, ...)  # Total: 10h
sol_3 = solve_fleet(stops, n_vehicles=3, ...)  # Total: 6.5h (mejor)
sol_4 = solve_fleet(stops, n_vehicles=4, ...)  # Total: 6.2h (marginal)

# Elegir sol_3 por mejor relación costo/tiempo
```

### Caso 3: Restricciones horarias + multi-camión

```python
# Con ventanas: algunos clientes deben atenderse en mañana (08:00-12:00)
# Un solo camión no puede cumplir todas las ventanas si son conflictivas

sol = solve_fleet(
    stops,
    n_vehicles=2,
    use_time_windows=True,  # Respeta ventanas de cada cliente
)
# Automáticamente asigna clientes de mañana a un camión
# y clients de tarde a otro
```

---

## Próximos pasos (MVP 7)

Para integrar **Logística Inversa Activa** con `solve_fleet`:

1. Activar `use_pickup_delivery=True` en `solve_single_truck()`
2. Pasar ese flag a `solve_fleet()` (ya está previsto en la firma)
3. Las dimensiones de capacidad se vuelven más sofisticadas:
   - Capacidad libre antes de parada = cap - (entregas totales)
   - Capacidad libre después = cap - (entregas) + (retornos)

Ejemplo futuro:
```python
sol = solve_fleet(
    stops,
    n_vehicles=2,
    use_pickup_delivery=True,  # Retornos = vol_retornable_l
)
# Respeta: en cada parada, Capacidad Libre ≥ 0 en todo momento
```

---

## Troubleshooting

### Status INFEASIBLE

**Posibles causas:**
1. Capacidad total de flota < suma de demandas
   - Solución: Aumentar `n_vehicles` o `truck_capacity_l`
2. Ventana horaria imposible de cumplir
   - Solución: Verificar `depot_open_s/close_s` vs ventanas de clientes
3. Distancias muy grandes (tiempo de tránsito prohibitivo)
   - Solución: Revisar matrices de distancia/tiempo

### Status FEASIBLE (no OPTIMAL)

- OR-Tools encontró solución pero agotó tiempo límite
- Solución: Aumentar `time_limit_s` (default 20s) o reducir `use_time_windows`

### Algunos vehículos vacíos

- `n_vehicles_used < n_vehicles`: Normal. OR-Tools usa solo lo necesario.
- Ejemplo: pediste 5 camiones pero con 3 es suficiente → solo carga 3

---

## Archivo de test

Incluido: `tests/test_fleet.py`

```bash
python3 tests/test_fleet.py
```

Valida:
- Todas las paradas están asignadas
- Capacidades por ruta se respetan
- Número de vehículos ≤ solicitados
- `solve_fleet` resuelve lo que `solve_single_truck` no podía
