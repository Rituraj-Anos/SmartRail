from datetime import datetime, timezone

from optimization.conflict_detector import ConflictDetector
from optimization.solvers.greedy_heuristic import GreedyHeuristic
from optimization.solvers.milp_solver import MILPSolver
from services.train_tracker.state import TrainState, TrainStatus
from simulator.data_generator import generate_scenario


def make_train_state(train_id, block_id, direction="UP", delay=0, priority=3):
    return TrainState(
        train_id=train_id,
        train_number=train_id,
        priority=priority,
        current_block_id=block_id,
        previous_block_id=None,
        speed_kmh=80.0,
        direction=direction,
        delay_seconds=delay,
        status=TrainStatus.RUNNING.value,
        scheduled_arrival=None,
        scheduled_departure=None,
        last_updated=datetime.now(timezone.utc).isoformat(),
        section_id="TEST-SEC",
    )


def test_greedy_solver_basic():
    section, trains = generate_scenario(num_stations=5, num_trains=3)
    solver = GreedyHeuristic(section)
    source_dest_map = {
        train.train_id: (
            section.stations[0].station_code,
            section.stations[-1].station_code,
        )
        for train in trains
    }
    schedules = solver.solve(trains, start_time_min=0, source_dest_map=source_dest_map)
    assert len(schedules) > 0


def test_milp_solver_basic():
    section, trains = generate_scenario(num_stations=5, num_trains=2)
    solver = MILPSolver(section, horizon_minutes=15, timeout_sec=2.0)
    source_dest_map = {
        train.train_id: (
            section.stations[0].station_code,
            section.stations[-1].station_code,
        )
        for train in trains
    }
    schedules = solver.solve(trains, start_time_min=0, source_dest_map=source_dest_map)
    assert isinstance(schedules, dict)


def test_detect():
    """ConflictDetector.detect() finds headway violation when two trains share a block."""
    cd = ConflictDetector()
    trains = [
        make_train_state("train_a", "BLOCK_05", priority=5),
        make_train_state("train_b", "BLOCK_05", priority=3),  # same block = conflict
        make_train_state("train_c", "BLOCK_10", priority=3),  # different block = ok
    ]
    report = cd.detect(trains)
    involved = [tid for c in report.conflicts for tid in c.trains_involved]
    assert "train_a" in involved
    assert "train_b" in involved
    assert report.requires_reoptimization is True
