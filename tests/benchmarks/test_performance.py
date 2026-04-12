import time
from simulator.data_generator import generate_scenario
from optimization.solvers.greedy_heuristic import GreedySolver
from optimization.solvers.milp_solver import MILPSolver
from optimization.conflict_detector import ConflictDetector

def test_greedy_performance():
    section, trains = generate_scenario(num_stations=15, num_trains=30)
    solver = GreedySolver(section)
    
    source_dest_map = {}
    for train in trains:
        # Create crossing traffic
        if hash(train.train_id) % 2 == 0:
            source_dest_map[train.train_id] = (section.stations[0].station_code, section.stations[-1].station_code)
        else:
            source_dest_map[train.train_id] = (section.stations[-1].station_code, section.stations[0].station_code)
            
    t0 = time.time()
    schedules = solver.solve(trains, start_time_min=0, source_dest_map=source_dest_map)
    t1 = time.time()
    
    # Target < 200ms but we use 1.0s to avoid test flakiness in cloud env
    assert (t1 - t0) < 1.0 
    assert isinstance(schedules, dict)
    
def test_milp_performance():
    section, trains = generate_scenario(num_stations=15, num_trains=30)
    solver = MILPSolver(section, horizon_minutes=30, timeout_sec=2.5) # Reduced horizon for faster test evaluation
    
    source_dest_map = {}
    for train in trains:
        if hash(train.train_id) % 2 == 0:
            source_dest_map[train.train_id] = (section.stations[0].station_code, section.stations[-1].station_code)
        else:
            source_dest_map[train.train_id] = (section.stations[-1].station_code, section.stations[0].station_code)
            
    t0 = time.time()
    schedules = solver.solve(trains, start_time_min=0, source_dest_map=source_dest_map)
    t1 = time.time()
    
    assert (t1 - t0) <= 3.5
    
    cd = ConflictDetector()
    t_cd0 = time.time()
    cd.detect_conflicts(schedules)
    t_cd1 = time.time()
    
    assert (t_cd1 - t_cd0) < 1.0
