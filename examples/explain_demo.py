"""Ejemplo de uso del módulo explain.py

Demuestra cómo generar explicaciones de rutas y empaquetamiento con LLM.
"""
import os
from src.vrp_solver import run_for_transporte, run_for_fleet
from src.explain import explain_solution, explain_fleet_solution


def example_single_truck_explanation():
    """Ejemplo: Explicar una ruta optimizada de 1 camión."""
    print("\n" + "="*80)
    print("EJEMPLO 1: Explicar ruta optimizada (1 camión)")
    print("="*80)
    
    # Obtener solución
    result = run_for_transporte(
        transporte_id=11561535,
        truck="6P",
        time_limit_s=10,
        use_time_windows=False,
    )
    
    # Generar explicación con LLM
    # (Nota: Requiere export GROQ_API_KEY='...')
    print("\n[INFO] Para habilitar explicaciones con IA, configura:")
    print("  export GROQ_API_KEY='tu_clave_de_groq_aqui'")
    print("\nDescargas disponible gratis desde: https://console.groq.com")
    
    print("\nEjemplo de llamada:")
    print("""
    from src.explain import explain_solution
    from src import config, distance_matrix
    import pandas as pd
    
    # Cargar datos
    canonical = pd.read_parquet(config.CANONICAL_PARQUET)
    geo = pd.read_parquet(config.GEOCODING_PARQUET)
    
    # Construir paradas
    from src.vrp_solver import build_stops_from_transporte
    stops = build_stops_from_transporte(11561535, canonical, geo)
    
    # Calcular matrices y resolver
    coords = [(config.DEPOT_LAT, config.DEPOT_LNG)] + [(s.lat, s.lng) for s in stops]
    time_mat, dist_mat = distance_matrix.get_matrix(coords)
    
    from src.vrp_solver import solve_single_truck
    cap_l, cap_kg = 1000, 500
    sol = solve_single_truck(stops, depot, cap_l, cap_kg, time_mat, dist_mat)
    
    # Generar explicación
    explanations = explain_solution(sol, stops, aspect="all", language="es")
    
    print("✓ Explicación de ruta:")
    print(explanations["route"])
    print("\\n✓ Explicación de empaquetamiento:")
    print(explanations["packaging"])
    """)


def example_fleet_explanation():
    """Ejemplo: Explicar una flota de múltiples camiones."""
    print("\n" + "="*80)
    print("EJEMPLO 2: Explicar distribución multi-camión (CVRP)")
    print("="*80)
    
    print("\nCódigo ejemplo:")
    print("""
    from src.vrp_solver import run_for_fleet
    from src.explain import explain_fleet_solution
    from src import config
    import pandas as pd
    
    # Obtener datos
    canonical = pd.read_parquet(config.CANONICAL_PARQUET)
    geo = pd.read_parquet(config.GEOCODING_PARQUET)
    
    from src.vrp_solver import build_stops_from_transporte, solve_fleet
    stops = build_stops_from_transporte(11561535, canonical, geo)
    
    # Resolver con 3 camiones
    from src import distance_matrix
    coords = [(config.DEPOT_LAT, config.DEPOT_LNG)] + [(s.lat, s.lng) for s in stops]
    time_mat, dist_mat = distance_matrix.get_matrix(coords)
    
    cap_l, cap_kg = 1000, 500
    fleet_sol = solve_fleet(stops, depot, n_vehicles=3, 
                            truck_capacity_l=cap_l, truck_capacity_kg=cap_kg,
                            time_matrix_s=time_mat, dist_matrix_m=dist_mat)
    
    # Generar explicación
    explanations = explain_fleet_solution(fleet_sol, stops, language="es")
    
    print("✓ Explicación de distribución de flota:")
    print(explanations["fleet"])
    print("\\n✓ Rutas por camión:")
    for i, route_info in enumerate(explanations["routes"]):
        print(f"  {route_info}")
    """)


if __name__ == "__main__":
    print("\n" + "="*80)
    print("MÓDULO DE EXPLICABILIDAD CON LLM (explain.py)")
    print("="*80)
    print("\nEste módulo genera explicaciones en lenguaje natural sobre:")
    print("  1. Por qué se eligió una ruta (orden de paradas)")
    print("  2. Cómo se distribuyó la flota multi-camión")
    print("  3. Cómo se manejó el empaquetamiento y carga viva")
    print("\nUsaGroq API + Llama 3.1 70B para generar explicaciones.")
    
    example_single_truck_explanation()
    example_fleet_explanation()
    
    print("\n" + "="*80)
    print("SETUP REQUERIDO")
    print("="*80)
    print("\n1. Instala groq:")
    print("   pip install groq>=0.4.1")
    print("\n2. Obtén API key gratis de Groq:")
    print("   https://console.groq.com")
    print("\n3. Configura en tu shell:")
    print("   export GROQ_API_KEY='gsk_...'")
    print("\n4. Verifica que funciona:")
    print("   python3 -c \"from groq import Groq; print('✓ Groq importado correctamente')\"")
    print("\n" + "="*80)
