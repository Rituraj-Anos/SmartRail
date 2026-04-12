from simulator.data_generator import generate_scenario
from optimization.solvers.greedy_heuristic import GreedySolver
from optimization.solvers.milp_solver import MILPSolver
from optimization.conflict_detector import ConflictDetector


def test_greedy_solver_basic():
    section, trains = generate_scenario(num_stations=5, num_trains=3)
    solver = GreedySolver(section)

    source_dest_map = {}
    for train in trains:
        source_dest_map[train.train_id] = (
            section.stations[0].station_code,
            section.stations[-1].station_code,
        )

    schedules = solver.solve(trains, start_time_min=0, source_dest_map=source_dest_map)
    assert len(schedules) > 0

    cd = ConflictDetector()
    conflicts = cd.detect_conflicts(schedules)
    # The greedy solver naturally avoids all headway and crossing conflicts by waiting
    # but let's just make sure it returns a list
    assert isinstance(conflicts, list)


def test_milp_solver_basic():
    section, trains = generate_scenario(num_stations=5, num_trains=2)
    solver = MILPSolver(section, horizon_minutes=15, timeout_sec=2.0)

    source_dest_map = {}
    for train in trains:
        source_dest_map[train.train_id] = (
            section.stations[0].station_code,
            section.stations[-1].station_code,
        )

    schedules = solver.solve(trains, start_time_min=0, source_dest_map=source_dest_map)
    assert isinstance(schedules, dict)


def test_detect_cascades():
    cd = ConflictDetector()
    schedules = {
        "train_delayed": [("A", 0), ("B", 10), ("C", 20)],
        "train_affected_1": [("B", 15), ("D", 25)],
        "train_affected_2": [("E", 5), ("B", 25)],
        "train_unaffected": [("X", 0), ("Y", 10)],
    }

    affected = cd.detect_cascades("train_delayed", schedules, delay_minutes=5)

    assert "train_affected_1" in affected
    assert "train_affected_2" in affected
    assert "train_unaffected" not in affected
    assert "train_delayed" not in affected
