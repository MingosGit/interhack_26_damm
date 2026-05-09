# Estrategia para ganar el reto Damm Smart Truck — INTERHACK BCN 2026

> Informe directo, sin rodeos. Cada decisión está justificada y todo el stack es **gratuito**.
> Pensado para desarrollar en **VSCode** con Python.

---

## 0. Lectura rápida del problema (lo que el jurado va a premiar)

El reto pide **dos cosas a la vez**: optimizar **ruta** y **carga** del camión, considerando logística inversa y restricciones reales (60% retornables, lonas laterales, capacidad 6/8 palets, ventanas horarias).

Los criterios de evaluación pesan así: **Aplicabilidad real 30% · Calidad técnica 25% · Impacto 20% · Creatividad 15% · Pitch 10%**.

Esto significa que **una solución técnicamente brillante pero ajena a la operativa real pierde** frente a una sólida que demuestre entender al chófer, al almacén y la descarga. Es el primer mensaje a interiorizar y a repetir en el pitch.

### Las tres ideas-fuerza que diferencian un proyecto ganador (y que la mayoría de equipos no verán)

**Idea 1 — Acceso lateral cambia todo el problema de carga.** Los camiones de DDI tienen lonas laterales. Eso significa que **NO hay restricción LIFO** (no hay que descargar lo último en entrar primero como un camión de puertas traseras). Se puede acceder a cualquier sección lateral. Esto convierte el camión en una **secuencia de "bahías" a lo largo del eje longitudinal**: cada bahía puede asignarse a un cliente o un clúster de clientes, en el orden de la ruta. La mayoría de equipos lo modelará como bin packing 3D clásico — error.

**Idea 2 — La logística inversa es un problema dinámico de volumen.** El 60% es retornable. A medida que se entrega, se libera espacio que se rellena con vacíos. No es un VRP con pickup-delivery normal: es un **balance volumétrico temporal**. La pregunta clave es: ¿en qué bahía habrá hueco en el momento de cada parada para meter los retornos? Esto se modela bien.

**Idea 3 — Carga híbrida (referencia + cliente) es el verdadero óptimo.** El brief dice literalmente "Equilibrio entre càrrega per referència i càrrega per client". El óptimo no es uno ni otro. Es: **clusterizar por barrio → asignar bahía por clúster → dentro de la bahía, agrupar por cliente → dentro del cliente, ordenar por referencia y estabilidad (barriles abajo, cajas medio, frágiles arriba)**. Esta jerarquía es defendible, simulable y explica el compromiso entre eficiencia de almacén y de descarga.

---

## 1. Composición del equipo (si tienes margen)

| Rol | Carga | Por qué |
|---|---|---|
| **Lead técnico / Optimización** | VRP + packing | Es el corazón. Necesita Python sólido. |
| **Datos / ETL / Geocoding** | Limpieza, geocoding, matriz | Sin datos limpios, no hay solución. |
| **Frontend / Dashboard** | Streamlit + visualizaciones | El jurado ve lo que ve. |
| **Negocio / Pitch** | KPIs, narrativa, slides | El 10% del pitch + el 30% de aplicabilidad real depende de aquí. |

Si sois 3, el de negocio se reparte entre todos. Si sois 2, el lead técnico cubre VRP+packing y el otro hace ETL+dashboard+pitch.

---

## 2. Stack técnico — la elección óptima en cada capa

Toda la pila es gratuita. He elegido lo que mejor combina **velocidad de desarrollo en hackathon + potencia + calidad visual de demo**.

### 2.1 Lenguaje base
**Python 3.11+**. No hay debate. Todas las librerías de optimización serias para VRP tienen Python como ciudadano de primera. Cualquier otra elección te penaliza en velocidad de prototipado.

### 2.2 Manipulación de datos
**Polars** (alternativa: pandas). Polars es 5-10x más rápido y tiene una API más limpia. Con 83k filas pandas también va, pero si quieres velocidad y syntax moderno, Polars. Si tu equipo no ha tocado Polars antes → pandas, no pierdas tiempo en la curva de aprendizaje en hackathon.

### 2.3 Optimizador de ruta (VRP)
**Google OR-Tools** — no hay competencia entre opciones gratuitas. Razones:
- Resuelve VRP, VRPTW (con ventanas horarias — tienes `Horarios_Entrega.xlsx`), CVRP (con capacidad), Pickup & Delivery (para retornables).
- Documentación excelente, ejemplos en Python, comunidad masiva.
- Battle-tested en producción (lo usa Lyft, etc.).
- Heurísticas + metaheurísticas (Guided Local Search, Tabu) ya implementadas: configuras un solver y obtienes solución decente en segundos para 25 paradas.

Instalación: `pip install ortools`. No hay setup extra.

Alternativa que NO recomiendo: **VROOM** (rust, requiere compilar/Docker) — más rápido en runtime pero te roba 2 horas de setup. Solo si vais sobrados de tiempo.

### 2.4 Matriz de distancias y tiempos
Necesitas tiempos de viaje reales por carretera entre clientes, no distancia en línea recta. Las direcciones del dataset están en Cataluña (Mollet, Vic, Manlleu, Montcada, etc.).

**Opción óptima: OpenRouteService API (free tier)** — 2.000 llamadas matriz/día, suficiente para el hackathon. Cero setup, key gratis en `openrouteservice.org`. Devuelve matriz `time + distance` en una sola llamada.

**Plan B si saturáis cuota: OSRM público** (`router.project-osrm.org`) — sin auth, rate-limited pero funciona. Endpoint `/table`.

**Plan C (más ambicioso, sólo si sois rápidos)**: levantar OSRM local con Docker y el PBF de Cataluña descargado de Geofabrik. 30 min de setup, llamadas ilimitadas, demo offline. Si queréis impresionar al jurado técnico, esto suma.

> **Atajo crucial**: cachea TODA la matriz en un JSON/Parquet la primera vez. Si tienes ~150 clientes únicos por jornada de prueba, son 150x150 = 22.500 entradas que sólo calculas una vez.

### 2.5 Geocodificación (dirección → coordenadas)
**Nominatim (OpenStreetMap)** vía librería `geopy`. Gratis, sin clave, rate-limit 1 req/seg. Para ~1369 direcciones únicas (lo que tienes en `Direcciones`) son ~23 minutos. Ejecuta de noche y cachea a Parquet. **NO** geocodifiques en cada run.

Si Nominatim falla o es lento: **photon.komoot.io** (también basado en OSM, sin rate-limit estricto).

### 2.6 Empaquetado del camión (3D / por bahías)
Aquí es donde **NO uses una librería genérica de 3D bin packing**. Es la trampa. La mayoría de libs (`py3dbp`, `3d-bin-packing`) ignoran:
- Orden de descarga (LIFO obligatorio en puertas traseras, **no aplica a tus camiones de lona lateral**).
- Estabilidad/apilabilidad por tipo (barril ≠ caja ≠ frágil).
- Acceso lateral por bahías.
- Carga dinámica con retornos.

**Estrategia recomendada**: implementar un **packer greedy custom** con esta lógica:

1. Modela el camión como una rejilla 1D de **bahías longitudinales** (p.ej. 6 u 8 posiciones de palet). Las dimensiones reales de un palet europeo son 80x120 cm.
2. Cada cliente tiene un volumen total y una "huella en palets" calculable desde `ZM040.XLSX` (que tiene `Volumen`, `Peso`, dimensiones por UMA — UN, CAJ, PAL, BOT).
3. Asigna clientes a bahías en **orden inverso al de descarga** desde el lado del camión, de modo que la primera parada tenga su bahía en el extremo más accesible.
4. Si un cliente cabe en menos de una bahía, agrupa con el siguiente cliente compatible (mismo barrio o ruta).
5. Dentro de cada bahía, apila por estabilidad: barriles/agua abajo, cajas medio, ligeros/frágiles arriba.

Esto se programa en ~200 líneas de Python y es defendible al milímetro frente al jurado.

Para visualizarlo en 3D: **Plotly** con `go.Mesh3d` o cubos `go.Box`. Suficiente, atractivo, integra en Streamlit sin fricción.

### 2.7 Visualización del mapa y ruta
**Folium** (wrapper Python de Leaflet). Razones: integración nativa con Streamlit (`streamlit-folium`), markers personalizables, polyline de la ruta, tooltips con info del cliente. Gratis e instantáneo.

Alternativa más fancy: **Pydeck** (deck.gl en Python) si quieres efectos 3D arc layers para impresionar. Folium es más que suficiente para ganar.

### 2.8 Dashboard / UI
**Streamlit** — sin alternativa razonable en hackathon. Página funcional en 2 horas, deploy gratuito a Streamlit Community Cloud (necesitas un repo de GitHub público). El jurado puede abrirlo en el móvil mientras presentas.

NO uses Flask/FastAPI + frontend separado. Es overkill y ladrón de tiempo.

### 2.9 Capa de explicabilidad (la guinda)
El brief pesa "explicabilitat" como criterio. Añade un módulo que genere **explicaciones en lenguaje natural** de cada decisión: "He puesto a Bar Pepe en la bahía 3 porque va a ser la 3ª parada y queda con peso similar al palet 4 para equilibrar el camión".

Opciones gratuitas:
- **Groq Cloud free tier**: Llama 3.3 70B Versatile, ~30 req/min sin pagar. Velocidad brutal (>500 tok/s). API key gratis en `groq.com`.
- **OpenRouter free models**: varios modelos gratis (incluyendo Llama 3.1 70B, Gemini Flash).
- **Ollama local** con Llama 3.1 8B si tu portátil tira: offline, ilimitado, pero más lento y menos calidad.

Recomendación: **Groq con Llama 3.3 70B**. Latencia mínima durante la demo, calidad de redacción excelente.

### 2.10 Productividad en VSCode
Extensiones obligatorias para acelerar:
- **Python + Pylance** (Microsoft).
- **Jupyter** para exploración rápida en `.ipynb`.
- **Continue.dev** o **Cline** (Roo) — asistentes de IA gratis si configuras un modelo gratuito (Groq, OpenRouter free, Ollama local). Aceleran mucho el código boilerplate.
- **GitLens** para coordinarte con el equipo.
- **Excel Viewer** (`grapecity.gc-excelviewer`) — abrir los xlsx sin salir de VSCode.

Setup de proyecto recomendado:
```
damm-smart-truck/
├── data/                  # raw inputs (los xlsx)
├── cache/                 # geocoding + matriz distancias (parquet)
├── src/
│   ├── etl.py            # limpieza datasets
│   ├── geocoding.py      # Nominatim + caché
│   ├── distance_matrix.py # OpenRouteService + caché
│   ├── vrp_solver.py     # OR-Tools
│   ├── packer.py         # algoritmo de bahías
│   ├── reverse_logistics.py
│   └── explain.py        # Groq client
├── app/
│   └── dashboard.py      # Streamlit
├── notebooks/             # exploración
├── requirements.txt
└── README.md
```

Usa `uv` (`pip install uv`) en lugar de pip — instala dependencias 10-100x más rápido. En hackathon no es trivial.

---

## 3. Hoja de ruta paso a paso (~36h)

### Hora 0–3 — Comprensión y ETL
Antes de tocar nada, **el equipo entero abre los datos juntos**. 30 minutos de mirarlo. Identificar:
- `Hackaton.xlsx` → Detalle entrega (82.849 filas, 2 meses, 889 transportes), Cabecera Transporte (cabeceras), Direcciones (1.369 clientes), ZONAS (zonificación logística), Materiales zubic (ubicación de cada material en almacén).
- `ZM040.XLSX` → maestro de materiales con dimensiones, volumen, peso por unidad de medida (UN/CAJ/PAL/BOT/BRL).
- `Horarios_Entrega.XLSX` → ventanas horarias por cliente y día semana.
- `Layout_Mollet.xlsx` → layout del almacén (rejilla de ubicaciones).
- Los .pptx → contexto de proceso (DR = ruta, DD = zona, repartidor 85x).

Construye un único DataFrame canónico unificado:
```
[fecha, transporte, ruta, repartidor, cliente_id, cliente_nombre, direccion, cp, ciudad,
 zona_dd, material, denominacion, cantidad, um, volumen_m3, peso_kg, retornable_bool,
 ventana_inicio, ventana_fin]
```

Calcula `volumen_m3` y `peso_kg` joinando `Detalle entrega` con `ZM040` por `Material` + `UMA`. Marca `retornable_bool` para BRL, BOT y otras claves recuperables (revisa la jerarquía `Jquía.productos` en ZM040).

**Output del bloque**: parquet limpio + estadísticas básicas (paradas/transporte, volumen/transporte, volumen retornable %).

### Hora 3–6 — Geocoding y matriz de distancias
- Geocodifica las 1.369 direcciones con Nominatim. Cachea a `cache/geocoding.parquet`.
- Para cada transporte simulado, calcula matriz de tiempos con OpenRouteService. Cachea por `(origen_lat,lng)→(destino_lat,lng)`.
- Define el **depot**: DDI Mollet del Vallès (lat 41.5408, lng 2.2128 aprox.).

### Hora 6–14 — VRP MVP con OR-Tools
**Hito 1 (h6-9)**: VRP básico de 1 camión, sin retornables, sin ventanas horarias. Toma 1 transporte real del dataset, resuelve y compara con el orden real del chófer. Métrica: km totales, tiempo total. Esto **te da el baseline contra el cual demostrarás mejora**.

**Hito 2 (h9-12)**: añade ventanas horarias (VRPTW) usando `Horarios_Entrega`. Añade restricción de capacidad (volumen del camión: 6 palets × ~480L ≈ 2.9 m³ útiles, ajusta con datos reales).

**Hito 3 (h12-14)**: multi-camión. Asigna automáticamente clientes a camiones (CVRP-TW). Compara con el reparto real entre los 18 repartidores existentes.

> **Truco para puntos extra**: deja la **función de coste configurable**. El usuario puede priorizar km, tiempo total, número de paradas o volumen retornado. Esto se vende solísimo en el pitch ("nuestro sistema acepta el criterio que la operativa quiera priorizar cada día").

### Hora 14–22 — Algoritmo de packing por bahías
- Modela el camión como N bahías (6 u 8) × ejes (X = longitudinal, Y = ancho 2 palets, Z = alto).
- Para cada parada en la ruta optimizada, calcula:
  - Volumen entregado (suma de volúmenes de cada material × cantidad).
  - Volumen recogido en retornos (estimación: 60% del volumen entregado de productos retornables, parámetro ajustable).
  - Huella en palets (`ceil(volumen_cliente / capacidad_palet)`).
- Asigna clientes a bahías **en orden inverso al de la ruta desde el lado de descarga**: cliente 1 en bahía más accesible, cliente N en la del fondo.
- Si un cliente cabe en < 1 palet, intenta combinar con el siguiente (mismo barrio si es posible — refuerza la coherencia).
- Validación: chequea que el volumen total ≤ capacidad camión y que cada bahía no excede peso máximo.

Visualización: render 3D con Plotly (`go.Mesh3d` o `go.Box`), códigos de color por cliente. Toggle para mostrar/ocultar retornos.

### Hora 22–28 — Logística inversa co-optimizada
Aquí es donde subes nota.

Modela la **evolución temporal del volumen**:
- Después de la parada k, el volumen libre es: `V_libre(k) = V_libre(k-1) + V_entregado(k) − V_retornado(k)`.
- Restricción dura: `V_libre(k) ≥ 0` siempre.
- Objetivo secundario: maximizar `Σ V_retornado(k)` (no dejar retornos en cliente).

Implementación práctica: extiende OR-Tools con `Pickup-Delivery` constraints y dimensiones de capacidad multidimensionales. Cada cliente puede ser entrega y recogida simultáneas.

Output adicional: gráfica de "ocupación del camión a lo largo de la ruta" — tabla y línea temporal. Esto es **oro puro para el pitch**.

### Hora 28–32 — Streamlit Dashboard
Tres páginas:

1. **Configurar reparto**: selector de fecha, selector de repartidor/ruta, slider de prioridad (tiempo vs km vs retornos), botón "Optimizar".
2. **Visualizar solución**: mapa Folium con ruta, lista ordenada de paradas con horarios, panel 3D del camión, KPIs vs baseline real (Δ km, Δ tiempo, Δ paradas reordenadas, % retornables recogidos).
3. **Explicación**: panel con la justificación generada por Groq de las decisiones clave ("esta ruta prioriza X porque...", "el camión se carga así porque...").

Despliega a Streamlit Community Cloud — el jurado puede abrirlo en su móvil.

### Hora 32–36 — Pitch + pulido
Estructura del pitch (8 minutos suelen):

1. **Problema en 30 s**: "DDI hace 889 rutas en 2 meses, ~10 entregas por camión, 60% retornable. Hoy ruta y carga se deciden por separado y por intuición."
2. **Insight clave en 1 min**: las 3 ideas-fuerza arriba (lateral, retornos dinámicos, híbrido).
3. **Demo en 4 min**: abre Streamlit, escoge una ruta REAL del dataset, optimiza, **compara lado a lado con el día real**. Muestra el camión en 3D. Muestra la explicación natural.
4. **Impacto en 1 min**: tabla de KPIs medidos sobre datos reales — "en 30 días simulados, X% menos km, Y% menos tiempo, Z% más retornables recogidos".
5. **Roadmap a piloto en 30 s**: integración con SAP/sistema actual, fase de driver-in-the-loop (chófer aprueba/edita y el sistema aprende), métricas de despliegue.
6. **Cierre en 30 s**: una frase memorable. Tipo "Hoy DDI carga camiones por referencia y ruta el chófer. Mañana, cada camión sale con la mejor combinación de las dos."

---

## 4. KPIs medibles (para que el impacto no sea hand-waving)

Calcúlalos sobre el dataset real comparando rutas reales vs optimizadas:

| KPI | Cómo medirlo |
|---|---|
| **Δ km totales por ruta** | Suma de aristas de la matriz OSRM. Esperado: −10/20%. |
| **Δ tiempo total ruta** | Idem en tiempo. |
| **Δ tiempo descarga estimado** | Modelo simple: `t_descarga = t_setup + n_movimientos × t_movimiento`. La carga por cliente reduce `n_movimientos`. Esperado: −20/40%. |
| **% retornables recogidos** | De los retornos posibles, cuántos caben con la solución vs aleatorio. Esperado: 95-100% vs 70-80% baseline. |
| **Aprovechamiento del camión** | Volumen usado / capacidad. Esperado: similar al baseline o ligeramente menor (trade-off aceptable). |
| **Coherencia de carga (índice propio)** | 1 si todos los productos del cliente i están en bahías contiguas, 0 si dispersos. Esperado: cerca de 1. |

Trae **una slide única con tabla comparativa** de estas métricas. Es la slide ganadora.

---

## 5. Trampas a evitar

- **No te enrredes con bin packing 3D genérico**. Pierdes 6 horas y el modelo no encaja con lonas laterales.
- **No geocodifiques en caliente durante la demo**. Cachea siempre.
- **No uses Google Maps API**. Cuesta dinero y hay alternativas gratis igual de buenas.
- **No optimices todo en un solo solver gigante**. Descompón: VRP primero, packing después. Es subóptimo teórico pero el óptimo conjunto es NP-hard y en hackathon no lo resuelves.
- **No subestimes el pitch**. Un proyecto medio bien presentado gana a uno excelente mal contado. Reserva las últimas 2-3 horas para ensayar.
- **No olvides el layout del almacén**. El brief lo lista como variable opcional pero **darle un guiño suma puntos en aplicabilidad real**: "si el almacén se reordenara con esta lógica de zonas espejo a las rutas, la preparación bajaría X%".

---

## 6. Extensiones para añadir si vais sobrados (cosa que dudo)

- **Aprendizaje del chófer**: log de ediciones manuales sobre la ruta propuesta + modelo simple que aprende preferencias (ej. "este chófer siempre evita la zona X a las 13h").
- **Modo "qué pasaría si"**: sliders interactivos en Streamlit para simular añadir/quitar clientes en caliente.
- **Predicción de tiempos de descarga**: pequeño modelo de regresión sobre datos históricos (volumen × tipo cliente → minutos). Si los datos lo soportan.
- **Optimización multi-día**: si un retorno no entra hoy, se prioriza mañana. Requiere ventana de planificación.

---

## 7. Checklist final (la noche antes de presentar)

- [ ] Dashboard desplegado y accesible desde URL pública.
- [ ] Repo en GitHub limpio con README explicativo.
- [ ] Ejecutar la demo en seco 3 veces sobre la misma ruta — sin sorpresas.
- [ ] Tabla de KPIs vs baseline impresa en una slide.
- [ ] Backup de los datos cacheados en disco — si OpenRouteService cae el día de la demo, sigues funcionando.
- [ ] Pitch ensayado, cronometrado, con una persona haciendo de jurado escéptico.
- [ ] Una frase memorable de cierre. Una sola.

---

## TL;DR

**Stack**: Python + Polars + OR-Tools + OpenRouteService + Folium + Plotly + Streamlit + Groq Llama 3.3.

**Diferenciador**: hibridez (cliente+referencia) + acceso lateral por bahías + retornos dinámicos como problema de volumen temporal.

**Métrica de impacto**: comparación directa contra rutas reales del dataset.

**Pitch**: insight → demo lado a lado → tabla KPIs → roadmap a piloto.

Si ejecutáis con disciplina, esto se gana.
