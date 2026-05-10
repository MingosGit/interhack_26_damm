import pandas as pd
from tqdm import tqdm
from loguru import logger
import warnings
import sys

# Suprimimos los prints y warnings normales para que la barra de progreso se vea limpia
warnings.filterwarnings("ignore")

from src import config
from src.vrp_solver import run_for_transporte

def procesar_todos_los_transportes(output_file="ahorros_damm.xlsx"):
    logger.info("Cargando base de datos canónica...")
    df_canonical = pd.read_parquet(config.CANONICAL_PARQUET)
    
    # Obtenemos todos los transportes únicos
    transportes = df_canonical["transporte"].dropna().unique()
    
    resultados = []
    errores = 0

    print(f"\n🚀 Iniciando simulación por lotes para {len(transportes)} transportes...\n")
    print("💡 TRUCO: Puedes presionar Ctrl+C en cualquier momento para detener la ejecución y guardar lo que lleve calculado.\n")
    
    # Envolvemos el bucle en un bloque try/except para capturar si lo cancelas
    try:
        # tqdm nos crea una barra de progreso en la terminal
        for t_id in tqdm(transportes, desc="Optimizando Rutas", unit="ruta"):
            try:
                # Ejecutamos el optimizador para cada camión
                res = run_for_transporte(int(t_id), truck="6P")
                
                # Solo guardamos si el optimizador encontró una solución factible
                if res["status"] in ["OPTIMAL", "FEASIBLE", "ROUTING_SUCCESS"]:
                    
                    # 1. MÉTRICA DISTANCIA
                    dist_real = res["baseline_dist_m"] / 1000
                    dist_opt = res["opt_dist_m"] / 1000
                    ahorro_km = dist_real - dist_opt
                    pct_km = (ahorro_km / dist_real * 100) if dist_real > 0 else 0
                    
                    # 2. MÉTRICA TIEMPO
                    tiempo_real = res["baseline_time_s"] / 3600
                    tiempo_opt = res["opt_time_s"] / 3600
                    ahorro_h = tiempo_real - tiempo_opt
                    pct_h = (ahorro_h / tiempo_real * 100) if tiempo_real > 0 else 0
                    
                    # 3. MÉTRICA MOVIMIENTOS
                    n_paradas = res["n_stops"]
                    movs_real = n_paradas * 1.5
                    movs_opt = n_paradas * 1.0
                    ahorro_mov = movs_real - movs_opt
                    pct_mov = (ahorro_mov / movs_real * 100) if movs_real > 0 else 0
                    
                    resultados.append({
                        "ID Transporte": t_id,
                        "Paradas": n_paradas,
                        "KM Reales": round(dist_real, 2),
                        "KM Optimizados": round(dist_opt, 2),
                        "AHORRO KM": round(ahorro_km, 2),
                        "% Ahorro KM": round(pct_km, 2),
                        "Horas Reales": round(tiempo_real, 2),
                        "Horas Optimizadas": round(tiempo_opt, 2),
                        "AHORRO HORAS": round(ahorro_h, 2),
                        "% Ahorro Horas": round(pct_h, 2),
                        "Movimientos Reales": movs_real,
                        "Movimientos Optimizados": movs_opt,
                        "AHORRO MOV.": ahorro_mov,
                        "% Ahorro Mov.": round(pct_mov, 2),
                        "Estado Solver": res["status"]
                    })
            except Exception as e:
                # Si un transporte falla, lo saltamos silenciosamente
                errores += 1
                pass

    except KeyboardInterrupt:
        # ¡AQUÍ ESTÁ LA MAGIA! Si detecta Ctrl+C, frena pero continúa con el guardado
        print("\n\n🛑 Simulación detenida manualmente por el usuario (Ctrl+C).")
        print("💾 Recopilando los cálculos parciales realizados hasta ahora...\n")

    # Convertimos la lista de resultados a un DataFrame de Pandas
    df_resultados = pd.DataFrame(resultados)
    
    # Calcular promedios generales y guardar
    if not df_resultados.empty:
        total_ahorro_km = df_resultados["AHORRO KM"].sum()
        total_ahorro_h = df_resultados["AHORRO HORAS"].sum()
        total_ahorro_mov = df_resultados["AHORRO MOV."].sum()
        
        prom_pct_km = df_resultados["% Ahorro KM"].mean()
        prom_pct_h = df_resultados["% Ahorro Horas"].mean()
        prom_pct_mov = df_resultados["% Ahorro Mov."].mean()
        
        print("\n" + "="*50)
        print("🎉 REPORTE DE SIMULACIÓN 🎉")
        print("="*50)
        print(f"Rutas procesadas con éxito: {len(resultados)}")
        print(f"Rutas fallidas/saltadas:    {errores}")
        print("-" * 50)
        print(f"AHORRO DISTANCIA:   {total_ahorro_km:,.2f} km  (Media: {prom_pct_km:.1f}%)")
        print(f"AHORRO TIEMPO:      {total_ahorro_h:,.2f} h   (Media: {prom_pct_h:.1f}%)")
        print(f"AHORRO MOVIMIENTOS: {total_ahorro_mov:,.0f} movs (Media: {prom_pct_mov:.1f}%)")
        print("="*50 + "\n")

        # Guardar a Excel
        df_resultados.to_excel(output_file, index=False)
        print(f"✅ Documento Excel generado con éxito en: {output_file}")
    else:
        print("⚠️ No se procesó ninguna ruta exitosamente, no hay datos que guardar.")

if __name__ == "__main__":
    procesar_todos_los_transportes()