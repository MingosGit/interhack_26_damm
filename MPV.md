# CLAUDE.md — Damm Smart Truck (INTERHACK BCN 2026)

Este fichero da contexto a Claude (o cualquier asistente IA en VSCode) sobre el proyecto.
Léelo entero antes de proponer cualquier cambio. Si vas a tocar datos o algoritmos, **vuelve aquí** para verificar el schema y las decisiones tomadas.

---

## 1. Visión general del proyecto

**Reto**: optimizar conjuntamente la **ruta de reparto** y la **configuración de carga** de los camiones de DDI (Distribución Directa Integral, grupo Damm), considerando logística inversa (60% del producto es retornable) y restricciones operativas reales (camiones de 6/8 palets con lonas laterales, ventanas horarias por cliente, layout del almacén).

**Objetivo del código**: un sistema decisional que dado un día, un repartidor y los pedidos asignados, proponga (a) el orden óptimo de visita y (b) cómo cargar físicamente el camión, con visualización y explicación.

**Tres ideas-fuerza del proyecto** (no las pierdas de vista):
1. **Lonas laterales = NO LIFO**. El camión se modela como N "bahías" longitudinales, no como una caja que se vacía por detrás.
2. **Retornables = balance volumétrico dinámico**. El camión libera espacio al entregar y lo rellena con vacíos. Es un problema temporal de ocupación.
3. **Carga híbrida** (cliente + referencia) jerárquica: barrio → bahía por clúster de clientes → dentro, por cliente → dentro, por estabilidad/peso (barriles abajo, frágiles arriba).

---

## 2. Stack técnico (DECIDIDO — no proponer cambios sin justificar)

| Capa | Elección | Notas |
|---|---|---|
| Lenguaje | Python 3.11+ | |
| Datos | `pandas` (o `polars` si el dev lo conoce) | 83k filas, no hay problema de memoria |
| VRP | `ortools` (Google OR-Tools) | VRPTW + Capacity + Pickup-Delivery |
| Matriz tiempo/distancia | OpenRouteService API (free tier 2000 req/día) o OSRM público | Con caché obligatorio en Parquet |
| Geocoding | `geopy` + Nominatim (OSM) | Rate-limit 1 req/s, cachear |
| Mapa | `folium` + `streamlit-folium` | |
| Visualización 3D camión | `plotly` (`go.Mesh3d` / `go.Box`) | |
| Dashboard | `streamlit` | Deploy a Streamlit Community Cloud |
| Explicación NL | Groq API (Llama 3.3 70B free tier) | Cliente: `groq` |
| Gestor paquetes | `uv` (preferido) o `pip` | |

**Comandos clave**:
```bash
# Setup
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Ejecución
python -m src.etl                          # Construir parquet canónico
python -m src.geocoding                    # Geocodificar y cachear
python -m src.distance_matrix              # Construir matriz, cachear
python -m src.vrp_solver --transport 11420136
python -m src.packer --transport 11420136
streamlit run app/dashboard.py
```

---

## 3. Estructura del proyecto

```
damm-smart-truck/
├── data/                          # raw inputs (xlsx originales — read-only)
│   ├── Hackaton.xlsx
│   ├── ZM040.XLSX
│   ├── Layout_Mollet.xlsx
│   ├── Horarios_Entrega.XLSX
│   ├── Reparto03_07_24.pptx
│   └── INTERHACK_Barcelona_2026.pptx
├── cache/                         # outputs cacheados (gitignore)
│   ├── geocoding.parquet          # cliente_id → (lat, lng)
│   ├── distance_matrix.parquet    # (lat1,lng1,lat2,lng2) → (time_s, dist_m)
│   └── canonical.parquet          # dataset unificado
├── src/
│   ├── __init__.py
│   ├── config.py                  # constantes: depot, capacidades, etc.
│   ├── etl.py                     # construye canonical.parquet
│   ├── geocoding.py               # Nominatim wrapper con caché
│   ├── distance_matrix.py         # OpenRouteService wrapper con caché
│   ├── vrp_solver.py              # OR-Tools VRPTW + Pickup-Delivery
│   ├── packer.py                  # algoritmo de bahías
│   ├── reverse_logistics.py       # cálculo volumen retornado
│   ├── kpis.py                    # baseline vs propuesta
│   └── explain.py                 # cliente Groq
├── app/
│   └── dashboard.py               # Streamlit
├── notebooks/                     # exploración (no en producción)
├── tests/
│   ├── test_etl.py
│   ├── test_packer.py
│   └── test_vrp.py
├── requirements.txt
├── .env                           # GROQ_API_KEY, ORS_API_KEY (gitignore)
└── README.md
```

---

## 4. Schema de los datos (todo lo que necesitas saber sobre los xlsx)

### 4.1 `Hackaton.xlsx` (input principal — 6.8 MB)

Es el **dataset operativo real**: 2 meses de entregas (feb-mar 2026), ~83k líneas.

#### Hoja `Detalle entrega` — 82.849 filas × 18 columnas

Una fila = una línea de pedido (1 material en 1 entrega de 1 cliente en 1 transporte de 1 día).

| Columna | Tipo | Ejemplo | Significado |
|---|---|---|---|
| `FECHA` | str | "30/01/2026" | Fecha del transporte. **OJO: viene como string DD/MM/YYYY**, parsear con `pd.to_datetime(s, format='%d/%m/%Y')`. |
| `Transporte` | int64 | 11420136 | ID del transporte (un camión-jornada). 889 únicos. |
| `Ruta` | str | "DA0216", "DR0054" | Código de ruta. 18 únicos. Los `DR####` son rutas de reparto regulares; los `DA####` son rutas auxiliares. |
| `Repartidor` | int64 | 855203 | ID del repartidor. 18 únicos. |
| `Destinatario mcía.` | str | "JACINT MAS CORNET" | **Nombre del REPARTIDOR**, no del cliente (sí, está mal nombrada — es histórico SAP). |
| `Entrega` | int64 | 827937019 | ID único de la entrega (un cliente en un transporte). |
| `Material` | str | "0CF0357" | Código del producto. Une con `ZM040.Material`. 1.489 únicos en uso. |
| `Denominación` | str | "BONKA ESSENTIA ESPRESSO ITALIANO 1KG" | Texto comercial del producto. |
| `Cantidad entrega` | int64 | 2 | Cantidad en la unidad de medida indicada. |
| `Un.medida venta` | str | "UN", "CAJ", "BRL"... | Unidad de medida. **Valores reales en el dataset**: `CAJ` (63.610), `UN` (8.420), `BRL` (6.553), `BOT` (3.013), `TB` (488), `PAK` (373), `ZPR` (257), `EST` (120), `PQ` (8), `TIR` (6), `BID` (1). |
| `Destinatario mcía..1` | int64 | 9100696143 | **Este SÍ es el ID real del cliente** (pandas renombra duplicados con `.1`). Une con `Direcciones.Cliente`. |
| `Nombre 1` | str | "LOS TERESITOS" | Nombre comercial del cliente. |
| `Nombre 2` | str | "LOS TERESITOS" | Suele ser igual a Nombre 1. |
| `Calle` | str | "Carrer Llevant 2" | Dirección. |
| `CP` | int64 | 8110 | Código postal. **OJO: int64 sin ceros a la izquierda**. Convertir a str con `f"{cp:05d}"`. |
| `Población` | str | "MONTCADA I REIXAC" | Localidad. 111 únicas. |
| `ZonaTransp` | str | "DD13100043" | Zona logística (DD = Direcciones de Distribución). |
| `ZonaTransp.1` | str | "MONTCADA I REIXAC" | Nombre de la zona (77 nulls). **OJO: contiene caracteres `\xa0` (NBSP)** entre palabras. Limpiar con `.replace('\xa0', ' ')`. |

**Llaves para joins**:
- `Material` → `ZM040.Material`
- `Destinatario mcía..1` → `Direcciones.Cliente`
- `(Material, Un.medida venta)` → `(ZM040.Material, ZM040.UMA)` para dimensiones

#### Hoja `Cabecera Transporte` — 8.927 filas × 8 columnas

Una fila por entrega. Útil para validar que cada entrega tiene un único repartidor/transporte.

| Columna | Significado |
|---|---|
| `Unnamed: 0` | Vacía — descartar |
| `Entrega` | ID entrega (PK aquí) |
| `Nº Transporte.` | ID transporte |
| `Creado el` | Fecha (string DD/MM/YYYY) |
| `Repartidor` | ID repartidor |
| `Unnamed: 5` | Nombre del repartidor |
| `Destinatario mcía.` | ID cliente |
| `Destinatario mcía..1` | Nombre cliente |

**No es esencial**: la información ya está agregada en `Detalle entrega`. Útil para sanity checks.

#### Hoja `Direcciones` — 1.368 filas × 6 columnas

Maestro de direcciones de cliente. Es lo que vas a **geocodificar**.

| Columna | Tipo | Ejemplo |
|---|---|---|
| `Cliente` | int64 | 9100681586 |
| `Nombre 1` | str | "AREA TRUCK ( SHELL )" |
| `Nombre 2` | str | "AREA TRUCK ( SHELL )" |
| `Calle` | str | "CARRER SUSQUEDA 18" |
| `CP` | int64 | 8500 |
| `Población` | str | "VIC" |

Sin nulls. Para geocoding, montar query:
```python
query = f"{calle}, {cp:05d} {poblacion}, Catalunya, España"
```

#### Hoja `ZONAS` — 1.203 filas × 14 columnas

**Estructura sucia**: las primeras columnas (`ZONAS`, `NOMBRE ZONAS`, `Zona Entrega`, `RutReal`, `Denominación`) tienen 1.134 nulls porque sólo están rellenas en las primeras ~70 filas (catálogo de zonas). El resto del fichero (`cliente zona`, `ZonaTransp`) es la asignación cliente → zona.

| Columna | Significado |
|---|---|
| `ZONAS` | Código de zona DD#### (sólo primeras filas) |
| `NOMBRE ZONAS` | Nombre legible de la zona (sólo primeras filas) |
| `cliente zona` | ID cliente |
| `ZonaTransp` | Zona del cliente |
| `ZonaTransp.1` | Duplicado de ZONAS |
| `Zona Entrega` | Nombre legible (duplicado de NOMBRE ZONAS) |
| `RutReal` | Ruta DR#### asociada |
| `Denominación` | Nombre de la ruta |

**Cómo procesarla**: dos sub-tablas. Filtra `ZONAS.notna()` para el catálogo zona→ruta, y `cliente_zona.notna()` para cliente→zona.

#### Hoja `Materiales zubic` — 1.489 filas × 8 columnas

Ubicación de cada material en el almacén.

| Columna | Significado |
|---|---|
| `Material` | Código producto |
| `Número de material` | Denominación |
| `Ce.` | Centro (siempre "D131" = Mollet) |
| `Alm.` | Almacén (1, 5, ...) |
| `UMB` | Unidad medida base |
| `Fabricante` | ID fabricante |
| `Número de un fabricante` | Nombre fabricante |
| `Ubic.` | **Código de ubicación física** (ej. "FA05A2", "ZCG", "PLV") |

**Ubicaciones especiales**:
- `ZCG` (1.131 entradas): casi todos los materiales — probablemente ubicación de defecto/picking general.
- `PLV` (49): otra zona genérica.
- `A0DISTRIDA` (44), `ENVASE` (42): zonas particulares.
- Códigos como `FA05A2` siguen patrón **letra + letra + 2 dígitos + letra + dígito** = pasillo/columna/altura.

### 4.2 `ZM040.XLSX` (maestro de materiales — 4.4 MB)

**Crítico para volumen y peso**. Una fila por (Material × UMA — unidad de manipulación).

48.457 filas × 22 columnas. Sólo ~4.476 filas matchean los materiales del dataset operativo, y de éstas, **sólo 1.384 tienen Volumen > 0**. Es decir: ~70% de los materiales del dataset NO tienen dimensiones reales en el maestro. **Hay que decidir un fallback** (ver sección 6).

| Columna | Tipo | Ejemplo | Significado |
|---|---|---|---|
| `Material` | str | "0CF0054" | Código producto |
| `TpMt` | str | "ZFIN" | Tipo material (ZFIN = producto terminado) |
| `UMA` | str | "CAJ", "PAL", "UN", "BOT", "BRL"... | Unidad de manipulación. **Valores presentes**: UN, ZPR, ZCE, CAJ, PAL, L, KG, BOT, ZPE, ZOP, ZPM, CAM, ZPA, MNT, G/L, ZS3, PAK, KGL, V%, GRP, BRL, EST, PQ, TIR, BOL, BID, BOX, UD, otros marginales. |
| `Contador` | float | 60.0 | Conversión: cuántas UMs base hay en una UMA. Ej: 1 PAL = 60 CAJ. |
| `Denom.` | float | 1.0 | Denominador de la conversión. |
| `Código EAN/UPC` | str | "88410793020334XX" | EAN. Muchos nulls. |
| `Longitud` | float | 100.0 | Dimensión 1 |
| `Unidad dimensión` | str | "CM" | Unidad de Longitud |
| `Ancho` | float | 120.0 | Dimensión 2 |
| `Unidad dimensión.1` | str | "CM" | Unidad de Ancho |
| `Altura` | float | 169.0 | Dimensión 3 |
| `Unidad dimensión.2` | str | "CM" | Unidad de Altura |
| `Unidad dimensión.3` | str | "CM" | (redundante) |
| `Volumen` | float | 475.2 | Volumen total |
| `UV` | str | "L", "HL", "DM3" | **Unidad de volumen — variable**. Convertir todo a litros: HL→×100, DM3→×1, L→×1, M3→×1000. |
| `UV.1` | str | (idem) | Redundante |
| `Peso bruto` | float | 1020.0 | Peso bruto |
| `Un` | str | "KG" | Unidad peso |
| `Un.1` | str | (idem) | Redundante |
| `Peso neto` | float | 0.0 | Peso neto (mayoría 0, no fiable) |
| `Un.2` | str | "KG" | Unidad peso neto |
| `Jquía.productos` | str | "00CF30ZZPCA1E4" | Jerarquía de productos. Primeros 4 chars = familia: `00AM` (1453 mat.), `00LM` (724), `00LI` (668), `00VE` (405), `00RF` (249), `00CZ` (195), `00AG` (188), `00ZU` (180), `00CF` (151)... |

**Unidades del retornable** (UMs que normalmente representan envases retornables que se recogen): `BRL` (barril), `BOT` (botella), `BID` (bidón). Adicionalmente, productos cuya `Denominación` contenga `RET`, `RETOR`, `BARRIL`, `ENVASE` son retornables (~42.368 filas en `Detalle entrega` — 51%).

### 4.3 `Layout_Mollet.xlsx` (layout almacén — 207 KB)

| Hoja | Estructura | Uso |
|---|---|---|
| `DDI MOLLET` | Rejilla 193×98 con códigos numéricos | Mapa visual del layout actual |
| `Hoja1` | Vacía | — |
| `Detalle` | Idem `DDI MOLLET` con más detalle (186×98) | Mapa con anotaciones |
| `RESUMEN DDI MOLLET` | Tabla 7×14 | Conteos por área/altura |
| `Hoja5` | Casi vacía | — |

**Resumen de capacidades (de la hoja RESUMEN)**:
- Interior: 2.055 ubicaciones totales (Estantería 1.194, Estantería compacta 240, Suelo 621).
- Exterior: 305 ubicaciones (aprox).
- Alturas: ALT(2), ALT(3), ALT(4), ALT(9).

Para procesar la rejilla: `pd.read_excel(..., sheet_name='DDI MOLLET', header=None)` → matriz numpy → renderizar como heatmap (`matplotlib`/`plotly`). Los números (1, 2, 3, 4, 9) representan tipos de zona/altura.

Este fichero es **opcional** para el optimizador — útil para el "extra" del reto sobre layout.

### 4.4 `Horarios_Entrega.XLSX` (ventanas horarias — 50 KB)

1.015 filas × 13 columnas. 240 clientes únicos.

| Columna | Significado |
|---|---|
| `Deudor` | ID cliente (no es el mismo formato que en Hackaton — 6 dígitos vs 10. Hay que validar mapping) |
| `Día semana` | 1-7 (probablemente lunes=1, domingo=7; valores presentes: 1,2,3,4,5,7) |
| `Turno` | 1 o 2 |
| `Horario inicia a` | `datetime.time` |
| `Horario termina a` | `datetime.time` |
| `Cierre Si/No` | "X" o NaN (82 X's) |
| `Nombre 1`, `Descripción*` | Metadatos |

**Atención**: el ID `Deudor` aquí es de 6 dígitos (104047), mientras que en `Detalle entrega.Destinatario mcía..1` es de 10 dígitos (9100696143). Hay que **investigar el mapping** antes de usarla. Probablemente los últimos N dígitos coinciden, o hay una tabla de equivalencia que no nos dieron.

### 4.5 `.pptx` (contexto)

`Reparto03_07_24.pptx` y `INTERHACK_Barcelona_2026.pptx` explican el proceso operativo de Damm/DDI. Resumen:
- Cliente (91...) → Ruta DR... → Zona DD... → Repartidor 85...
- Camiones: **6 palets (×11), 8 palets (×4), Furgoneta 3 palets (×1)** en la flota Mollet.
- Productos organizados por tipo y apilabilidad: **barriles, retornables, latas, cajas**.
- Orden de rotación por volumen.

---

## 5. Constantes globales del proyecto (en `src/config.py`)

```python
# DEPOT (almacén DDI Mollet)
DEPOT_LAT = 41.5408
DEPOT_LNG = 2.2128
DEPOT_NAME = "DDI Mollet del Vallès"

# CAMIONES (de la flota Mollet)
TRUCKS = {
    "6P":  {"palets": 6, "count": 11, "vol_m3": 14.4, "peso_max_kg": 6500},
    "8P":  {"palets": 8, "count": 4,  "vol_m3": 19.2, "peso_max_kg": 8500},
    "FUR": {"palets": 3, "count": 1,  "vol_m3": 7.2,  "peso_max_kg": 3500},
}
# Volumen: ~2.4 m³ por palet (estándar palet europeo cargado a ~2m altura)

# PALET EUROPEO
PALET_LARGO_CM  = 120
PALET_ANCHO_CM  = 80
PALET_ALT_MAX_CM = 200  # altura máx de carga

# RETORNABLES
UM_RETORNABLES = {"BRL", "BOT", "BID"}
KEYWORDS_RETORNABLES = ["RET", "RETOR", "BARRIL", "ENVASE", "VACIO"]
RATIO_RETORNO_DEFECTO = 0.6  # del brief

# CONVERSION VOLUMEN A LITROS
UV_TO_LITERS = {"L": 1.0, "HL": 100.0, "DM3": 1.0, "M3": 1000.0, "ML": 0.001, "CL": 0.01}
```

Verifica las dimensiones reales con la persona de Damm si surgen mentores. Las que tienes son aproximaciones razonables.

---

## 6. Reglas de ETL (cómo construir el dataset canónico)

Implementar en `src/etl.py` y volcar a `cache/canonical.parquet`.

**Pipeline**:

1. Leer `Hackaton.xlsx > Detalle entrega`. Renombrar columnas duplicadas (`Destinatario mcía..1` → `cliente_id`).
2. Parsear `FECHA` a `datetime`.
3. Convertir `CP` a string con padding: `df['cp'] = df['CP'].apply(lambda x: f"{int(x):05d}")`.
4. Limpiar `\xa0` en strings: `df['poblacion'] = df['Población'].str.replace('\xa0', ' ', regex=False)`.
5. Join con `ZM040`: por `(Material, UMA=Un.medida venta)`. Left join.
6. **Manejar dimensiones nulas** (~70% de las filas):
   - Si `Volumen > 0`: usar como base.
   - Si `Volumen == 0` y existe registro para mismo Material con UMA="PAL": dividir vol_palet entre `Contador` para estimar.
   - Si nada disponible: usar volumen por defecto según UMA (`CAJ`: 30L, `UN`: 1L, `BRL`: 50L, `BOT`: 1L, `PAK`: 5L). Loggear estos casos.
7. Convertir todos los volúmenes a **litros** con `UV_TO_LITERS`.
8. Calcular `volumen_total_l = cantidad_entrega * volumen_unitario_l`.
9. Calcular `peso_total_kg` análogamente con `Peso bruto`.
10. Marcar `retornable_bool`:
    ```python
    df['retornable'] = (
        df['Un.medida venta'].isin(UM_RETORNABLES) |
        df['Denominación'].str.contains('|'.join(KEYWORDS_RETORNABLES), case=False, na=False)
    )
    ```
11. Agregar a nivel **entrega-cliente** (no a nivel línea):
    - Por `(Transporte, Entrega, cliente_id)` → suma volumen, suma peso, lista de materiales, % retornable.
12. Persistir a `cache/canonical.parquet`.

**Output esperado**: ~7.500 filas (entregas únicas) con columnas:
```
fecha, transporte, ruta, repartidor, repartidor_nombre, entrega_id, cliente_id,
cliente_nombre, calle, cp, poblacion, zona_dd,
volumen_total_l, peso_total_kg, n_materiales,
volumen_retornable_l, materiales_json (lista detallada por si se necesita)
```

---

## 7. MVPs del proyecto (DETALLADOS — usar como roadmap de PRs)

Cada MVP es un **incremento funcional independiente**. No saltes uno sin haber cerrado el anterior.

### MVP 1 — Pipeline de datos canónico

**Objetivo**: a partir de los xlsx originales, generar `cache/canonical.parquet` y un report de calidad.

**Ficheros tocados**: `src/etl.py`, `src/config.py`.

**Funciones públicas a implementar**:
```python
def load_raw() -> dict[str, pd.DataFrame]:
    """Carga todos los xlsx en un dict {sheet_name: df}."""

def clean_detalle_entrega(df: pd.DataFrame) -> pd.DataFrame:
    """Renombra cols, parsea fechas, limpia strings, normaliza CP."""

def enrich_with_zm040(df: pd.DataFrame, zm040: pd.DataFrame) -> pd.DataFrame:
    """Join + cálculo de volumen/peso por línea, con fallback de dims faltantes."""

def mark_retornables(df: pd.DataFrame) -> pd.DataFrame:
    """Añade columna retornable_bool."""

def aggregate_by_entrega(df: pd.DataFrame) -> pd.DataFrame:
    """Agrupa de líneas a nivel entrega-cliente."""

def build_canonical() -> pd.DataFrame:
    """Pipeline completo. Persiste a cache/canonical.parquet y devuelve df."""
```

**Criterios de aceptación**:
- [ ] El parquet existe y tiene >7.000 filas.
- [ ] No hay nulls en columnas críticas: `cliente_id`, `transporte`, `ruta`, `volumen_total_l`, `peso_total_kg`.
- [ ] `volumen_total_l` siempre > 0 (ningún material sin estimación).
- [ ] Test `tests/test_etl.py` pasa: incluye al menos un caso conocido a mano (ej. transporte 11420136 → 1 entrega cliente 9100696143 → volumen esperado X).
- [ ] Genera un fichero `cache/data_quality_report.txt` con: % de materiales con dim real, top 20 materiales sin dim, conteo de retornables por UM.
- [ ] Comando `python -m src.etl` lo ejecuta de cero.

**Riesgo principal**: el join Material+UMA tiene baja cobertura. Sin un fallback robusto, el VRP no podrá comparar capacidades. Loggear y auditar.

---

### MVP 2 — Geocoding

**Objetivo**: asociar a cada cliente unas coordenadas (lat, lng).

**Ficheros tocados**: `src/geocoding.py`.

**Funciones públicas a implementar**:
```python
def load_geocoding_cache() -> pd.DataFrame:
    """Devuelve df con cliente_id, lat, lng, status, query_used. Vacío si no existe."""

def geocode_address(query: str) -> tuple[float, float] | None:
    """Llama Nominatim (geopy). Respeta rate-limit 1 req/s. Reintenta una vez si falla."""

def geocode_all(addresses: pd.DataFrame, force: bool = False) -> pd.DataFrame:
    """Geocodifica todas las direcciones únicas. Cachea cada éxito al instante (no esperar al final)."""

def fallback_to_cp_centroid(cp: str) -> tuple[float, float] | None:
    """Si Nominatim falla con dirección completa, geocodificar sólo el CP."""
```

**Criterios de aceptación**:
- [ ] `cache/geocoding.parquet` existe con columnas `cliente_id, lat, lng, status, query_used`.
- [ ] Cubre al menos el 95% de los clientes que aparecen en `canonical.parquet`.
- [ ] Para los no cubiertos, hay registro de `status='failed'` y razón.
- [ ] Geocoding incremental: si vuelves a ejecutar, no re-pide los ya cacheados (idempotente).
- [ ] Validar visualmente: 5 muestras random plotteadas en `folium` (script de smoke test) caen en Cataluña, no en Mongolia.
- [ ] Test que verifica el rate-limit (no hace más de 1 req/seg al servidor real — usar mock para tests unitarios).

**Riesgo principal**: direcciones mal formateadas (S/N, sin número, abreviaturas catalanas). Implementar fallback a CP centroid.

---

### MVP 3 — Matriz de tiempos y distancias

**Objetivo**: dado un set de coordenadas, devolver la matriz `M[i][j] = (segundos, metros)`.

**Ficheros tocados**: `src/distance_matrix.py`.

**Funciones públicas a implementar**:
```python
def load_distance_cache() -> dict[tuple[float,float,float,float], tuple[int,int]]:
    """Cache en memoria + persistencia."""

def get_matrix(coords: list[tuple[float,float]], provider: str = "ors") -> tuple[np.ndarray, np.ndarray]:
    """Devuelve (tiempos_segundos, distancias_metros) como matrices NxN."""

def _query_ors_matrix(coords: list[tuple[float,float]]) -> dict:
    """Llama OpenRouteService /v2/matrix/driving-hgv. Formato HGV (heavy goods vehicle)."""

def _query_osrm_matrix(coords: list[tuple[float,float]]) -> dict:
    """Fallback: OSRM público /table/v1/driving."""
```

**Criterios de aceptación**:
- [ ] Para una lista de 25 coordenadas conocidas (incluyendo el depot), devuelve matriz 25×25.
- [ ] Cache: `cache/distance_matrix.parquet` con índice `(lat1,lng1,lat2,lng2)`. La segunda llamada con las mismas coords es instantánea (no toca red).
- [ ] Maneja errores HTTP (429 rate-limit, 5xx) con back-off exponencial y reintentos.
- [ ] Soporta dos providers (ORS y OSRM) intercambiables. Variable de entorno `MATRIX_PROVIDER=ors|osrm`.
- [ ] Si una coord falla en el routing real, se rellena con `haversine` en línea recta + factor 1.4 (estimación urbana) y se loggea.
- [ ] Usa **endpoint HGV** (camión) en ORS, no `driving-car` — diferencia significativa de tiempos.
- [ ] Test que valida estructura del output (matriz simétrica en distancia? no necesariamente, calles unidireccionales).

---

### MVP 4 — Solver VRP base (1 camión, sin retornables, sin ventanas)

**Objetivo**: dado un transporte real, reordenar las paradas para minimizar tiempo total.

**Ficheros tocados**: `src/vrp_solver.py`.

**Funciones públicas a implementar**:
```python
@dataclass
class Stop:
    cliente_id: str
    lat: float
    lng: float
    volumen_l: float
    peso_kg: float
    volumen_retornable_l: float
    ventana_inicio: int | None  # segundos desde medianoche
    ventana_fin: int | None
    tiempo_servicio_s: int  # tiempo de descarga estimado

@dataclass
class Solution:
    ordered_stops: list[Stop]
    total_time_s: int
    total_distance_m: int
    status: str  # "OPTIMAL", "FEASIBLE", "INFEASIBLE"
    raw_solver_output: dict  # para debug

def solve_single_truck(
    stops: list[Stop],
    depot: tuple[float, float],
    truck_capacity_l: float,
    truck_capacity_kg: float,
    time_matrix_s: np.ndarray,
    dist_matrix_m: np.ndarray,
    max_route_time_s: int = 8 * 3600,
    use_time_windows: bool = False,
    use_pickup_delivery: bool = False,
) -> Solution: ...

def build_stops_from_transporte(transporte_id: int, canonical: pd.DataFrame) -> list[Stop]: ...
```

**Criterios de aceptación**:
- [ ] `python -m src.vrp_solver --transport 11420136` devuelve:
  - Lista ordenada de paradas con tiempos estimados de llegada.
  - Tiempo total y distancia total.
  - Comparación con orden real del dataset (mismo transporte): `Δ tiempo`, `Δ distancia`.
- [ ] Solver corre en < 30 segundos para 25 paradas en local.
- [ ] Si hay solo 2 paradas, devuelve solución trivial sin error.
- [ ] Tests cubren: caso vacío (1 parada solo el depot), caso 5 paradas con solución conocida, caso de capacidad excedida (debe devolver INFEASIBLE).
- [ ] Documentar en docstring qué heurística usa: `PATH_CHEAPEST_ARC` para inicial + `GUIDED_LOCAL_SEARCH` con time-limit como metaheurística. Justifica las elecciones.

**Patrón OR-Tools recomendado** (referencia mental para el LLM):
```python
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

manager = pywrapcp.RoutingIndexManager(num_nodes, num_vehicles, depot_index)
routing = pywrapcp.RoutingModel(manager)

# Coste = tiempo
def time_callback(from_idx, to_idx):
    return time_matrix[manager.IndexToNode(from_idx)][manager.IndexToNode(to_idx)]
transit_idx = routing.RegisterTransitCallback(time_callback)
routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

# Capacidad
routing.AddDimensionWithVehicleCapacity(
    demand_callback_index, 0, [int(truck_capacity_l)], True, "Capacity")

search = pywrapcp.DefaultRoutingSearchParameters()
search.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
search.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
search.time_limit.seconds = 20

solution = routing.SolveWithParameters(search)
```

---

### MVP 5 — VRP con ventanas horarias

**Objetivo**: respetar las ventanas de `Horarios_Entrega` (cuando se logre mapear el ID).

**Ficheros tocados**: `src/vrp_solver.py` (extensión).

**Pre-requisito**: investigar el mapping `Deudor` (Horarios) ↔ `cliente_id` (Detalle). Hipótesis a probar:
1. Los últimos 6 dígitos de `cliente_id` (10 dígitos) coinciden con `Deudor` (6 dígitos). Verificar joineando.
2. Si no coincide, usar nombre (`Nombre 1`) con fuzzy matching como fallback.
3. Si tampoco, asumir ventana abierta `[00:00, 23:59:59]` por defecto.

**Criterios de aceptación**:
- [ ] `Stop.ventana_inicio/fin` se pueblan desde `Horarios_Entrega` cuando hay match (loggear el % de match).
- [ ] El solver con `use_time_windows=True` respeta las ventanas: ningún stop llega antes de su `inicio`.
- [ ] Si no hay solución factible (ventanas demasiado restrictivas), devuelve `INFEASIBLE` con explicación de qué stop fue infactible.
- [ ] Tiempo de servicio (`tiempo_servicio_s`) se modela: por defecto 10 minutos por parada + 2 minutos por palet entregado. Ajustable.
- [ ] Tests: caso con 3 paradas y ventanas que fuerzan un orden específico.

---

### MVP 6 — Packer por bahías

**Objetivo**: dada una ruta ordenada (output del VRP), generar la configuración física del camión.

**Ficheros tocados**: `src/packer.py`.

**Modelo conceptual**:
- El camión se modela como una secuencia de `n_bahías` (= número de palets que caben longitudinalmente). Para 6P: 6 bahías. Para 8P: 8.
- Cada bahía tiene capacidad de `~2.4 m³`, `~1100 kg`.
- La bahía 1 es la **más accesible desde la puerta lateral del lado de descarga** (asumimos lado derecho del camión = primer punto descargado).

**Funciones públicas a implementar**:
```python
@dataclass
class BayItem:
    cliente_id: str
    materiales: list[dict]  # cada uno: material, denominacion, cantidad, um, vol_l, peso_kg, retornable
    volumen_l: float
    peso_kg: float
    altura_estimada_cm: float
    tipo_dominante: str  # "BARRIL", "CAJA", "BOTELLA", "MIXTO"

@dataclass
class Bay:
    index: int  # 0-based
    items: list[BayItem]
    vol_usado_l: float
    peso_kg: float
    capacidad_l: float
    capacidad_kg_max: float
    espacio_libre_l: float

@dataclass
class TruckLoad:
    bays: list[Bay]
    truck_type: str
    vol_total_l: float
    peso_total_kg: float
    volumen_libre_post_descarga: list[float]  # serie temporal post cada parada
    coherencia_cliente: float  # 0-1, índice de "items del mismo cliente en bahías contiguas"

def pack_truck(
    ordered_stops: list[Stop],
    truck_type: str,
    strategy: str = "hybrid",  # "by_client" | "by_reference" | "hybrid"
) -> TruckLoad: ...
```

**Algoritmo "hybrid" (recomendado)**:

1. Asigna cada cliente a una bahía respetando el **orden inverso de descarga**: cliente 1 → bahía 0 (más accesible), cliente 2 → bahía 1, etc.
2. Si un cliente excede 1 bahía, ocupa varias contiguas.
3. Si un cliente cabe en < 0.5 bahía, intenta combinarlo con el siguiente cliente **si comparten zona** (mismo CP o `ZonaTransp`). Marcar como "compartida".
4. Dentro de cada bahía, ordena los materiales del cliente por **estabilidad/peso**:
   - Capa inferior (Z=0): barriles, packs de agua, latas pesadas.
   - Capa media: cajas estándar.
   - Capa superior: ligeros, frágiles, packs pequeños.
5. Reserva espacio en bahías futuras (más alejadas del lado de descarga) para los retornables que se irán recogiendo. Cálculo: `vol_libre_post_parada_k = Σ(vol_entregado_1..k) − Σ(vol_retornado_1..k)`.

**Criterios de aceptación**:
- [ ] Para cualquier ruta ordenada de N paradas, devuelve un `TruckLoad` con todas las paradas asignadas.
- [ ] Si volumen total > capacidad camión → raise `VolumenExcedidoError` con sugerencia de truck mayor.
- [ ] `coherencia_cliente >= 0.85` para rutas con clientes pequeños (la mayoría en una sola bahía).
- [ ] La bahía 0 contiene SIEMPRE al cliente de la primera parada.
- [ ] Tests: caso con 6 clientes pequeños (1 por bahía), caso con 1 cliente grande (ocupa 3 bahías), caso con muchos clientes mini (combinaciones).
- [ ] Función `to_3d_visualization(truck_load) -> plotly.Figure` que produce un render 3D con cubos coloreados por cliente, ejes etiquetados (largo, ancho, alto), tooltip con info por bahía.

**Detalle visual**: usar paleta consistente (un color por cliente). Render isométrico, no perspectiva — es más legible. Marcar la "puerta de descarga" con flecha y texto.

---

### MVP 7 — Logística inversa integrada

**Objetivo**: optimizar también la recogida de retornables, no solo entrega.

**Ficheros tocados**: `src/reverse_logistics.py`, extensión de `src/vrp_solver.py`.

**Funciones públicas a implementar**:
```python
def estimate_returns_per_stop(stop: Stop, ratio: float = RATIO_RETORNO_DEFECTO) -> float:
    """Volumen estimado de retornos en cada parada, basado en el % retornable del producto entregado."""

def temporal_volume_profile(load: TruckLoad, ordered_stops: list[Stop]) -> list[float]:
    """Devuelve serie de ocupación del camión post cada parada k."""

def solve_with_pickup(
    stops: list[Stop],
    depot: tuple[float, float],
    truck_capacity_l: float,
    time_matrix_s: np.ndarray,
    ...
) -> Solution:
    """Extiende el VRP con dimensión adicional 'volumen_retornado' que no debe exceder 
    la capacidad post-descarga en cada momento."""
```

**Criterios de aceptación**:
- [ ] El solver con pickup-delivery activo respeta que en ningún punto la ocupación supere la capacidad.
- [ ] El TruckLoad reserva espacio para retornos en bahías que se van vaciando.
- [ ] Output adicional: gráfica `plotly` de "ocupación del camión a lo largo de la ruta": área apilada con outbound (decreciente) y returns (creciente).
- [ ] KPI: `% retornables recogidos = vol_retornado_total / vol_retorno_estimado_total`. Objetivo > 90%.

---

### MVP 8 — KPIs y comparación con baseline

**Objetivo**: cuantificar la mejora vs el día real (orden real de entregas en `Detalle entrega`).

**Ficheros tocados**: `src/kpis.py`.

**Funciones públicas a implementar**:
```python
@dataclass
class KPIComparison:
    transporte_id: int
    fecha: date
    n_paradas: int
    
    # Real (baseline)
    real_distancia_m: int
    real_tiempo_s: int
    real_n_movimientos_descarga: int
    real_pct_retornables_recogidos: float
    
    # Optimizado
    opt_distancia_m: int
    opt_tiempo_s: int
    opt_n_movimientos_descarga: int
    opt_pct_retornables_recogidos: float
    
    # Deltas
    delta_distancia_pct: float
    delta_tiempo_pct: float
    delta_movimientos_pct: float
    delta_retornables_pp: float  # puntos porcentuales
    
def compare(transporte_id: int) -> KPIComparison: ...

def batch_compare(fecha_inicio: date, fecha_fin: date) -> pd.DataFrame:
    """Compara todos los transportes en el rango. Devuelve df listo para slide."""
```

**Definición de `n_movimientos_descarga`**:
- Real: asumir orden aleatorio en camión cargado por referencia → `n_movimientos = Σ_clientes (n_materiales_cliente × factor_dispersión)` donde `factor_dispersión` = 1.5 (heurística).
- Optimizado: `n_movimientos = Σ_clientes n_materiales_cliente × 1.0` (acceso directo a su bahía).

**Criterios de aceptación**:
- [ ] Función `batch_compare` genera tabla con todos los transportes de los 2 meses.
- [ ] Promedios agregados disponibles: `df.mean()`. Esperado: `delta_distancia_pct < -10%`, `delta_tiempo_pct < -10%`, `delta_movimientos_pct < -25%`.
- [ ] Plot resumen: histograma de mejoras por transporte. Detectar outliers (transportes que empeoran — investigar).
- [ ] Output a CSV: `cache/kpi_comparison.csv` para incluir en pitch.

---

### MVP 9 — Dashboard Streamlit

**Objetivo**: UI completa para la demo del jurado.

**Ficheros tocados**: `app/dashboard.py`.

**Páginas (sidebar de Streamlit)**:

1. **Inicio**: explicación del proyecto, KPIs agregados (los del MVP 8 sobre el dataset completo).
2. **Optimizar reparto**:
   - Sidebar: fecha selector, repartidor selector → carga `transporte_id` automáticamente. Mostrar info del transporte (n paradas, volumen total).
   - Sliders: prioridad (tiempo / distancia / retornos), camión (6P/8P/Furg).
   - Botón "Optimizar". Spinner mientras calcula.
   - Output (tabs):
     - **Mapa**: Folium con depot + paradas numeradas + polyline ruta. Tooltips con info.
     - **Camión 3D**: render Plotly 3D del TruckLoad.
     - **Tabla paradas**: orden, cliente, hora estimada llegada, volumen entregado, retornables.
     - **Comparación**: tabla 3 columnas (Métrica | Real | Optimizado | Δ%).
     - **Explicación**: texto generado por Groq sobre las decisiones (carga lazy, botón "Generar explicación").
3. **Explorar datos**: gráficas pandas-profiling-style. % retornables por familia, distribución de volumen por transporte, etc.
4. **Layout almacén** (extra): heatmap del layout Mollet con propuesta de reorganización.

**Criterios de aceptación**:
- [ ] Lanzable con `streamlit run app/dashboard.py`.
- [ ] Carga inicial < 5 segundos.
- [ ] La página "Optimizar" funciona end-to-end: seleccionas, optimiza, todos los tabs renderizan sin error.
- [ ] El mapa Folium se ve bien en móvil (responsive).
- [ ] Cacheo de cómputos pesados con `@st.cache_data` (matriz, geocoding) y `@st.cache_resource` (modelos).
- [ ] Manejo de errores: si un transporte no tiene clientes geocodificados, mostrar mensaje claro, no crash.
- [ ] Deploy a Streamlit Community Cloud con repo público de GitHub. URL accesible para el jurado.

---

### MVP 10 — Capa de explicación (Groq)

**Objetivo**: generar texto natural que justifique cada decisión, alimentando el criterio de "explicabilidad" del reto.

**Ficheros tocados**: `src/explain.py`.

**Funciones públicas a implementar**:
```python
def explain_route(solution: Solution, baseline: Solution | None = None) -> str: ...

def explain_loading(load: TruckLoad, ordered_stops: list[Stop]) -> str: ...

def explain_tradeoffs(comparison: KPIComparison) -> str: ...
```

**Plantilla de prompt** (en `src/explain.py` como constante):
```
Eres un experto en logística de distribución. Explica al equipo operativo de DDI 
por qué la propuesta de ruta y carga es buena, en máximo 3 párrafos cortos.

Datos de la ruta:
- Camión tipo: {truck_type}
- Número de paradas: {n_stops}
- Distancia total: {dist_km} km (vs {baseline_km} km del orden real → {delta_pct}%)
- Volumen total entregado: {vol_l} L
- % retornable: {pct_ret}%

Asignación a bahías:
{bay_assignments_table}

Tradeoffs detectados:
{tradeoffs}

Reglas:
- Sé concreto y operativo, lenguaje de chófer/jefe de almacén.
- Menciona explícitamente al menos un tradeoff (lo que se sacrifica).
- No inventes datos. No uses bullets.
```

**Criterios de aceptación**:
- [ ] Cliente Groq inicializado desde `GROQ_API_KEY` en `.env`.
- [ ] Caching de respuestas en `cache/explanations.json` por hash del input → no se gasta cuota en la demo.
- [ ] Manejo de timeout (5 s) y fallback a explicación template-based si Groq no responde.
- [ ] Tests con Groq mockeado.
- [ ] La explicación generada referencia datos reales (números concretos), no genérico.

---

## 8. Convenciones de código

- **Type hints en TODAS las funciones públicas**. Usar `from __future__ import annotations`.
- **Logging** con `loguru` (más simple que stdlib): `from loguru import logger`.
- **Tests** con `pytest`. Cada MVP cierra con tests pasando.
- **Errores** específicos: definir excepciones custom en `src/exceptions.py` (`VolumenExcedidoError`, `GeocodingFailedError`, etc.). NO usar `Exception` genérica.
- **Configuración** en `src/config.py`. Nunca hardcodear paths o credenciales en otros módulos.
- **Datos** siempre en formato Parquet para caches (no CSV). Usar `pd.to_parquet(path, engine='pyarrow', compression='snappy')`.
- **No comitear** `.env`, `cache/`, `.venv/`, `__pycache__/`. `.gitignore` debe contemplarlo.
- **Naming**: snake_case para variables y funciones, PascalCase para clases, UPPER_CASE para constantes. Sin abreviaturas oscuras.

---

## 9. Trampas conocidas (NO REPETIR)

1. **Columnas duplicadas en `Detalle entrega`**: pandas las renombra automáticamente con `.1`. NO confundir `Destinatario mcía.` (nombre repartidor) con `Destinatario mcía..1` (cliente_id).
2. **Caracteres `\xa0` (NBSP)** en columnas de texto. Limpiar siempre.
3. **CP como int**: pierde el cero a la izquierda (Barcelona = 08xxx). Convertir a string con padding al instante.
4. **ZM040 incompleto**: ~70% de materiales sin dimensiones reales. Implementar fallback obligatorio.
5. **Unidad de volumen variable** (`UV` puede ser L, HL, DM3, M3). Normalizar a litros desde el principio.
6. **Mapping cliente_id ↔ Deudor**: NO ASUMIR que coinciden directamente. Investigar antes de usar `Horarios_Entrega`.
7. **Rate limit Nominatim**: si no respetas 1 req/s te banean por horas. Usar `time.sleep(1.1)` o `RateLimiter` de `geopy`.
8. **Rate limit OpenRouteService**: 2.000 req/día, 40 req/min en matriz. Si saturas, fallback a OSRM.
9. **OR-Tools requiere INTs**: capacidades, demandas, costes — todos enteros. Multiplicar floats por 1.000 si necesitas decimales (ej. litros con resolución de mL: `int(vol_l * 1000)`).
10. **OR-Tools y depot**: el depot es el nodo 0. Si tienes 25 paradas + 1 depot, num_nodes = 26 y el depot entra/sale. No olvidar.
11. **Streamlit y reruns**: cada interacción del usuario re-ejecuta el script. Cachea con `@st.cache_data` agresivamente.
12. **Folium dentro de Streamlit**: usar `streamlit-folium` (`st_folium`), NO `folium.Map._repr_html_()` con `st.html`.
13. **3D LIFO no aplica aquí**: no pierdas tiempo con librerías de bin packing 3D que asumen LIFO. Tus camiones tienen lonas laterales.

---

## 10. Comandos rápidos para Claude/asistente IA

Cuando trabajes en este repo, recuerda:

- "Antes de escribir código que toque datos, abre `cache/canonical.parquet` y mira las primeras filas con `df.head()`."
- "Si añades una nueva columna al canonical, actualiza el schema en este `CLAUDE.md` (sección 6)."
- "Si tocas el VRP solver, ejecuta `python -m src.vrp_solver --transport 11420136` y verifica que la solución sigue siendo razonable."
- "Si tocas el packer, ejecuta el smoke test con un transporte conocido y mira el render 3D — debe verse coherente."
- "No introduzcas nuevas dependencias sin justificarlo en el `requirements.txt` con un comentario."
- "Cuando dudes del schema de un xlsx, vuelve a la sección 4 de este fichero. NO abras el xlsx desde código sin saber qué hojas y columnas tiene."

---

## 11. Definition of Done del proyecto

El proyecto está listo para presentar cuando:

- [ ] Los 10 MVPs están cerrados con sus criterios de aceptación.
- [ ] Hay un transporte real (ej. `11420136`) que se puede demostrar end-to-end en < 90 segundos.
- [ ] La tabla agregada de KPIs muestra mejora medible vs baseline en los 2 meses de datos.
- [ ] El dashboard está deployado en Streamlit Cloud y accesible vía URL pública.
- [ ] El repo de GitHub tiene README con: descripción, instalación, comandos, screenshots.
- [ ] Las explicaciones generadas son coherentes y referenciables a datos.
- [ ] La presentación de pitch tiene una slide única con la tabla de KPIs vs baseline.
