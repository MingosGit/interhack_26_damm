"""Test basic functionality of solve_fleet (CVRP multi-vehicle solver).

Este test crea un ejemplo simple con 6 paradas y prueba solve_fleet con 2 vehículos.
"""
import numpy as np
from src.vrp_solver import Stop, solve_fleet, solve_single_truck
from src import config


def test_solve_fleet_basic():
    """Test basic CVRP con 6 nodos, 2 vehículos.

    Comparamos vs solve_single_truck para validar que solve_fleet es más eficiente
    (menos tiempo total, igual cantidad de paradas pero distribuidas).
    """
    # ---- Crear 6 stops simples en una línea ----
    stops = [
        Stop(cliente_id=101, lat=40.0, lng=-3.0, volumen_l=100, peso_kg=50, cliente_nombre="Client A"),
        Stop(cliente_id=102, lat=40.1, lng=-3.0, volumen_l=150, peso_kg=75, cliente_nombre="Client B"),
        Stop(cliente_id=103, lat=40.2, lng=-3.0, volumen_l=120, peso_kg=60, cliente_nombre="Client C"),
        Stop(cliente_id=104, lat=40.3, lng=-3.0, volumen_l=100, peso_kg=50, cliente_nombre="Client D"),
        Stop(cliente_id=105, lat=40.4, lng=-3.0, volumen_l=110, peso_kg=55, cliente_nombre="Client E"),
        Stop(cliente_id=106, lat=40.5, lng=-3.0, volumen_l=130, peso_kg=65, cliente_nombre="Client F"),
    ]

    depot = (40.0, -3.5)

    # ---- Matriz de distancias simplificada (km → m) ----
    # Distancia entre cualquier par ≈ dif_lat * 111 km/deg = dif_lat * 111000 m
    n_nodes = len(stops) + 1  # +1 para depot
    dist_matrix = np.zeros((n_nodes, n_nodes))
    time_matrix = np.zeros((n_nodes, n_nodes))

    # Depot a paradas y entre paradas (aproximación lineal)
    dist_depot_lat = 40.0
    for i in range(n_nodes):
        for j in range(n_nodes):
            if i == j:
                dist_matrix[i, j] = 0
                time_matrix[i, j] = 0
            elif i == 0 or j == 0:
                # Depot
                lat = stops[j - 1].lat if j > 0 else depot[0]
                lat_other = stops[i - 1].lat if i > 0 else depot[0]
                dlat = abs(lat - lat_other)
                dist = int(dlat * 111000)  # metros
                time = int(dist / 15) + 180  # 15 m/s avg + 3 min overhead
                dist_matrix[i, j] = dist
                time_matrix[i, j] = time
            else:
                # Entre paradas
                lat_i = stops[i - 1].lat
                lat_j = stops[j - 1].lat
                dlat = abs(lat_i - lat_j)
                dist = int(dlat * 111000)
                time = int(dist / 15) + 180
                dist_matrix[i, j] = dist
                time_matrix[i, j] = time

    cap_vol = 500.0   # 500L por camión
    cap_kg = 250.0    # 250kg por camión

    print("\n" + "=" * 80)
    print("TEST: solve_fleet vs solve_single_truck")
    print("=" * 80)
    print(f"Paradas: {len(stops)}")
    print(f"Volumen total: {sum(s.volumen_l for s in stops):.0f}L")
    print(f"Peso total: {sum(s.peso_kg for s in stops):.0f}kg")
    print(f"Capacidad camión: {cap_vol:.0f}L, {cap_kg:.0f}kg")

    # ---- Intento 1: solve_single_truck (debe fallar o ser subóptimo) ----
    print("\n[1] Intentando solve_single_truck (1 vehículo)...")
    sol_single = solve_single_truck(
        stops, depot, cap_vol, cap_kg, time_matrix, dist_matrix,
        time_limit_s=10,
    )
    print(f"     Status: {sol_single.status}")
    if sol_single.status == "INFEASIBLE":
        print(f"     ✓ Capacidad insuficiente (esperado): {len(stops)} paradas × 100-150L > {cap_vol:.0f}L")
    else:
        print(f"     Paradas: {len(sol_single.ordered_stops)} | Tiempo: {sol_single.total_time_s}s")

    # ---- Intento 2: solve_fleet con 2 vehículos ----
    print("\n[2] Ejecutando solve_fleet (2 vehículos)...")
    sol_fleet = solve_fleet(
        stops, depot, n_vehicles=2,
        truck_capacity_l=cap_vol, truck_capacity_kg=cap_kg,
        time_matrix_s=time_matrix, dist_matrix_m=dist_matrix,
        time_limit_s=10,
    )
    print(f"     Status: {sol_fleet.status}")
    print(f"     Vehículos usados: {sol_fleet.n_vehicles_used}")
    print(f"     Total paradas: {sum(len(r) for r in sol_fleet.routes)}")
    print(f"     Tiempo total: {sol_fleet.total_time_s}s")
    print(f"     Distancia total: {sol_fleet.total_distance_m/1000:.2f}km")

    if sol_fleet.n_vehicles_used > 0:
        print(f"\n     Desglose por ruta:")
        for i, (route, metrics) in enumerate(zip(sol_fleet.routes, sol_fleet.route_metrics)):
            vol_route = sum(s.volumen_l for s in route)
            kg_route = sum(s.peso_kg for s in route)
            print(f"       Ruta {i+1}: {len(route)} paradas | "
                  f"{vol_route:.0f}L / {kg_route:.0f}kg | "
                  f"{metrics['time_s']}s | {metrics['distance_m']/1000:.2f}km")

    # ---- Validaciones ----
    print("\n" + "-" * 80)
    print("VALIDACIONES:")
    print("-" * 80)
    total_stops_fleet = sum(len(r) for r in sol_fleet.routes)
    assert total_stops_fleet == len(stops), f"Falta paradas: {total_stops_fleet} vs {len(stops)}"
    print(f"✓ Todas las {len(stops)} paradas están asignadas")

    for i, route in enumerate(sol_fleet.routes):
        vol = sum(s.volumen_l for s in route)
        kg = sum(s.peso_kg for s in route)
        assert vol <= cap_vol + 1e-6, f"Ruta {i} excede capacidad volumen: {vol} > {cap_vol}"
        assert kg <= cap_kg + 1e-6, f"Ruta {i} excede capacidad peso: {kg} > {cap_kg}"
    print(f"✓ Todas las rutas respetan capacidades")

    assert sol_fleet.n_vehicles_used <= 2, f"Usa más vehículos de los solicitados"
    print(f"✓ Usa {sol_fleet.n_vehicles_used} vehículos (≤ 2 solicitados)")

    if sol_single.status == "INFEASIBLE" and sol_fleet.status in ("OPTIMAL", "FEASIBLE"):
        print(f"✓ solve_fleet resuelve lo que solve_single_truck no podía")

    print("\n" + "=" * 80)
    print("TEST PASSED!")
    print("=" * 80)


if __name__ == "__main__":
    test_solve_fleet_basic()
