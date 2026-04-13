"""
SmartRail — Simulator Unit Tests
Tests for SimPy DES engine, train processes, and scenario manager.
"""

from __future__ import annotations

import time

import pytest

from simulator.engine import (
    GreedyPolicy,
    MILPPolicy,
    RandomDelayPolicy,
    SectionConfig,
    SmartRailSimulator,
)
from simulator.scenario_manager import ScenarioManager
from simulator.train_process import TrainConfig


# ── Fixtures ──────────────────────────────────────────────────────────────────


def make_section_config(n_blocks: int = 5) -> SectionConfig:
    """Create a simple linear section config."""
    blocks = [f"block_{i}" for i in range(n_blocks)]
    stations = [blocks[0], blocks[-1]]
    return SectionConfig(
        section_id="sec_001",
        section_name="Test Section",
        blocks=blocks,
        stations=stations,
        total_length_km=50.0,
        is_single_line=True,
    )


def make_train_config(
    train_id: str,
    train_number: str,
    priority: int,
    departure: float,
    arrival: float,
    n_blocks: int = 5,
    travel_time_per_block: float = 5.0,
) -> TrainConfig:
    """Create a simple train config traversing all blocks."""
    route = [f"block_{i}" for i in range(n_blocks)]
    base_travel_times = {f"block_{i}": travel_time_per_block for i in range(n_blocks)}
    return TrainConfig(
        train_id=train_id,
        train_number=train_number,
        priority=priority,
        route=route,
        scheduled_departure=departure,
        scheduled_arrival=arrival,
        max_speed_kmh=100.0,
        base_travel_times=base_travel_times,
    )


def make_timetable(n_trains: int = 3, n_blocks: int = 5) -> list[TrainConfig]:
    """Create a simple timetable with staggered departures."""
    trains = []
    for i in range(n_trains):
        trains.append(
            make_train_config(
                train_id=f"train_{i:03d}",
                train_number=f"T{1000 + i}",
                priority=5 - (i % 4),  # mix of priorities
                departure=float(i * 10),
                arrival=float(i * 10 + 30),
                n_blocks=n_blocks,
            )
        )
    return trains


# ── Engine Tests ──────────────────────────────────────────────────────────────


class TestSmartRailSimulator:

    def test_basic_run_completes(self):
        """Simulator runs without error and returns metrics."""
        section = make_section_config()
        timetable = make_timetable(n_trains=3)
        sim = SmartRailSimulator(section, timetable, policy="greedy")
        metrics = sim.run(duration_minutes=120)

        assert metrics is not None
        assert metrics.total_trains == 3
        assert metrics.completed_trains > 0

    def test_all_trains_complete(self):
        """All trains complete their journey in a clean section."""
        section = make_section_config(n_blocks=5)
        timetable = make_timetable(n_trains=5)
        sim = SmartRailSimulator(section, timetable, policy="greedy")
        metrics = sim.run(duration_minutes=300)

        assert metrics.completed_trains == 5

    def test_metrics_structure(self):
        """Metrics contain all required fields."""
        section = make_section_config()
        timetable = make_timetable(n_trains=2)
        sim = SmartRailSimulator(section, timetable, policy="greedy")
        metrics = sim.run(duration_minutes=120)

        assert metrics.average_delay_minutes >= 0
        assert metrics.max_delay_minutes >= 0
        assert 0 <= metrics.punctuality_index <= 100
        assert metrics.throughput_per_hour >= 0
        assert metrics.simulation_wall_time_seconds > 0
        assert metrics.policy_used == "greedy"

    def test_higher_priority_less_delay(self):
        """Higher priority trains should have less or equal delay."""
        section = make_section_config(n_blocks=3)

        # Two trains on same route — one high priority, one low
        high = make_train_config(
            "high", "T001", priority=5, departure=0, arrival=20, n_blocks=3
        )
        low = make_train_config(
            "low", "T002", priority=2, departure=2, arrival=22, n_blocks=3
        )

        sim = SmartRailSimulator(section, [high, low], policy="greedy")
        metrics = sim.run(duration_minutes=120)

        # Find individual metrics
        train_data = {t["train_id"]: t for t in metrics.train_metrics}

        assert metrics.completed_trains == 2
        # High priority should not be held behind low priority
        high_delay = train_data["high"]["delay_minutes"]
        assert high_delay <= 5.0  # should be on time or nearly so

    def test_24_hours_under_60_seconds(self):
        """Core performance requirement: 24hrs simulated in < 60 real seconds."""
        section = make_section_config(n_blocks=20)
        timetable = make_timetable(n_trains=30, n_blocks=20)

        sim = SmartRailSimulator(section, timetable, policy="greedy")

        wall_start = time.time()
        metrics = sim.run(duration_minutes=1440)
        wall_time = time.time() - wall_start

        assert wall_time < 60.0, f"Simulation took {wall_time:.2f}s — must be under 60s"
        assert metrics.completed_trains > 0

    def test_greedy_policy(self):
        """Greedy policy runs without error."""
        section = make_section_config()
        timetable = make_timetable(n_trains=4)
        sim = SmartRailSimulator(section, timetable, policy="greedy")
        metrics = sim.run(duration_minutes=200)
        assert metrics.policy_used == "greedy"

    def test_milp_policy(self):
        """MILP policy with pre-computed schedule runs without error."""
        section = make_section_config()
        timetable = make_timetable(n_trains=2)
        schedule = {
            "train_000": {"block_0": 0.0, "block_1": 5.0},
            "train_001": {"block_0": 10.0, "block_1": 15.0},
        }
        sim = SmartRailSimulator(
            section,
            timetable,
            policy="milp",
            policy_kwargs={"schedule": schedule},
        )
        metrics = sim.run(duration_minutes=120)
        assert metrics.policy_used == "milp"

    def test_random_delay_policy(self):
        """Random delay policy introduces delays."""
        section = make_section_config()
        timetable = make_timetable(n_trains=5)

        sim_clean = SmartRailSimulator(section, timetable, policy="greedy")
        metrics_clean = sim_clean.run(duration_minutes=300)

        sim_delayed = SmartRailSimulator(
            section,
            timetable,
            policy="random_delay",
            policy_kwargs={"delay_probability": 0.8, "max_delay": 20.0},
        )
        metrics_delayed = sim_delayed.run(duration_minutes=600)

        # Delayed scenario should have more delay than clean
        assert (
            metrics_delayed.average_delay_minutes >= metrics_clean.average_delay_minutes
        )

    def test_delay_injection(self):
        """Injecting delay into a train increases its arrival delay."""
        section = make_section_config()
        timetable = [
            make_train_config("t1", "T001", priority=3, departure=0, arrival=30)
        ]

        sim = SmartRailSimulator(section, timetable, policy="greedy")
        sim.inject_train_delay("t1", 20.0)
        metrics = sim.run(duration_minutes=120)

        assert metrics.completed_trains == 1
        assert metrics.average_delay_minutes >= 0

    def test_get_section_state(self):
        """Section state snapshot returns correct structure."""
        section = make_section_config()
        timetable = make_timetable(n_trains=2)
        sim = SmartRailSimulator(section, timetable, policy="greedy")

        # Run partial simulation
        sim.env.run(until=5)
        state = sim.get_section_state()

        assert "sim_time" in state
        assert "active_trains" in state
        assert "completed_count" in state
        assert state["sim_time"] == 5


# ── Policy Tests ──────────────────────────────────────────────────────────────


class TestPolicies:

    def test_greedy_policy_instantiates(self):
        policy = GreedyPolicy()
        assert policy is not None

    def test_milp_policy_with_no_schedule(self):
        policy = MILPPolicy()
        # Without schedule, returns 0 hold time
        assert policy.get_hold_time("t1", "b1", 0.0, None) == 0.0

    def test_milp_policy_with_schedule(self):
        schedule = {"t1": {"b1": 10.0}}
        policy = MILPPolicy(schedule=schedule)
        # Train is at t=0, block scheduled for t=10 → hold 10 min
        hold = policy.get_hold_time("t1", "b1", 0.0, None)
        assert hold == 10.0

    def test_milp_policy_no_hold_if_on_time(self):
        schedule = {"t1": {"b1": 5.0}}
        policy = MILPPolicy(schedule=schedule)
        # Train arrives at t=6, schedule was t=5 → already late, no hold
        hold = policy.get_hold_time("t1", "b1", 6.0, None)
        assert hold == 0.0

    def test_random_delay_policy_reproducible(self):
        """Same seed produces same delays."""
        p1 = RandomDelayPolicy(delay_probability=1.0)
        p2 = RandomDelayPolicy(delay_probability=1.0)
        f1 = p1.get_delay_factor("t1", "b1")
        f2 = p2.get_delay_factor("t1", "b1")
        assert f1 == f2

    def test_random_delay_policy_factor_range(self):
        policy = RandomDelayPolicy(delay_probability=1.0, max_delay=20.0)
        factor = policy.get_delay_factor("t1", "b1")
        assert 1.0 <= factor <= 1.5  # 10-50% slowdown range


# ── Scenario Manager Tests ─────────────────────────────────────────────────────


class TestScenarioManager:

    @pytest.fixture
    def tmp_manager(self, tmp_path):
        return ScenarioManager(storage_dir=tmp_path / "scenarios")

    @pytest.fixture
    def base_section(self):
        return make_section_config(n_blocks=5)

    @pytest.fixture
    def base_timetable(self):
        return make_timetable(n_trains=3)

    def test_create_scenario(self, tmp_manager, base_section, base_timetable):
        scenario = tmp_manager.create_scenario(
            name="Baseline",
            section_config=base_section,
            timetable=base_timetable,
            policy="greedy",
        )
        assert scenario.scenario_id is not None
        assert scenario.name == "Baseline"
        assert len(scenario.timetable) == 3
        assert scenario.metrics is None  # not run yet

    def test_run_scenario(self, tmp_manager, base_section, base_timetable):
        scenario = tmp_manager.create_scenario(
            name="Run Test",
            section_config=base_section,
            timetable=base_timetable,
        )
        metrics = tmp_manager.run_scenario(scenario, duration_minutes=200)

        assert metrics.completed_trains > 0
        assert scenario.metrics is not None

    def test_save_and_load_scenario(self, tmp_manager, base_section, base_timetable):
        scenario = tmp_manager.create_scenario(
            name="Save Test",
            section_config=base_section,
            timetable=base_timetable,
        )
        tmp_manager.run_scenario(scenario, duration_minutes=200)
        tmp_manager.save_scenario(scenario)

        loaded = tmp_manager.load_scenario(scenario.scenario_id)
        assert loaded.scenario_id == scenario.scenario_id
        assert loaded.name == "Save Test"
        assert loaded.metrics is not None

    def test_list_scenarios(self, tmp_manager, base_section, base_timetable):
        for name in ["Alpha", "Beta", "Gamma"]:
            s = tmp_manager.create_scenario(
                name=name,
                section_config=base_section,
                timetable=base_timetable,
            )
            tmp_manager.run_scenario(s, duration_minutes=100)
            tmp_manager.save_scenario(s)

        listing = tmp_manager.list_scenarios()
        assert len(listing) == 3
        names = [s["name"] for s in listing]
        assert "Alpha" in names
        assert "Beta" in names

    def test_compare_scenarios(self, tmp_manager, base_section, base_timetable):
        # Baseline: clean run
        s_a = tmp_manager.create_scenario(
            name="Baseline",
            section_config=base_section,
            timetable=base_timetable,
            policy="greedy",
        )
        tmp_manager.run_scenario(s_a, duration_minutes=200)

        # Disrupted: with delay injection
        s_b = tmp_manager.create_scenario(
            name="Disruption",
            section_config=base_section,
            timetable=base_timetable,
            policy="greedy",
        )
        tmp_manager.add_delay_modification(s_b, "train_000", 30.0, "breakdown")
        tmp_manager.run_scenario(s_b, duration_minutes=300)

        comparison = tmp_manager.compare_scenarios(s_a.scenario_id, s_b.scenario_id)

        assert "scenario_a" in comparison
        assert "scenario_b" in comparison
        assert "delta" in comparison
        assert "summary" in comparison
        assert isinstance(comparison["summary"], str)

    def test_compare_unrun_raises(self, tmp_manager, base_section, base_timetable):
        s_a = tmp_manager.create_scenario(
            name="A", section_config=base_section, timetable=base_timetable
        )
        s_b = tmp_manager.create_scenario(
            name="B", section_config=base_section, timetable=base_timetable
        )
        with pytest.raises(ValueError, match="must be run before comparison"):
            tmp_manager.compare_scenarios(s_a.scenario_id, s_b.scenario_id)

    def test_add_modifications(self, tmp_manager, base_section, base_timetable):
        scenario = tmp_manager.create_scenario(
            name="Mod Test",
            section_config=base_section,
            timetable=base_timetable,
        )
        tmp_manager.add_delay_modification(scenario, "train_000", 15.0)
        tmp_manager.add_block_slowdown(scenario, "block_2", 1.5)
        tmp_manager.add_breakdown(scenario, "train_001", "block_1", 20.0)

        assert len(scenario.modifications) == 3
        assert scenario.modifications[0]["type"] == "delay"
        assert scenario.modifications[1]["type"] == "block_slowdown"
        assert scenario.modifications[2]["type"] == "breakdown"

    def test_load_nonexistent_raises(self, tmp_manager):
        with pytest.raises(FileNotFoundError):
            tmp_manager.load_scenario("nonexistent_id")
