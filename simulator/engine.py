"""
SmartRail — Discrete Event Simulation Engine
SimPy-based simulator modeling full section traffic.
Can run 24 hours of section traffic in under 60 seconds.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import simpy

from simulator.train_process import TrainConfig, TrainProcess, TrainStatus

logger = logging.getLogger(__name__)


@dataclass
class SectionConfig:
    """Configuration for the simulated railway section."""

    section_id: str
    section_name: str
    blocks: list[str]  # ordered list of block IDs
    stations: list[str]  # subset of blocks that are stations
    total_length_km: float = 100.0
    is_single_line: bool = True


@dataclass
class SimulationMetrics:
    """Aggregated metrics from a completed simulation run."""

    total_trains: int = 0
    completed_trains: int = 0
    average_delay_minutes: float = 0.0
    max_delay_minutes: float = 0.0
    on_time_trains: int = 0  # arrived within 5 min of schedule
    punctuality_index: float = 0.0  # % on time
    total_conflicts_detected: int = 0
    total_hold_time_minutes: float = 0.0
    throughput_per_hour: float = 0.0
    simulation_wall_time_seconds: float = 0.0
    policy_used: str = "greedy"

    # Per-train breakdown
    train_metrics: list[dict] = field(default_factory=list)


class OptimizationPolicy:
    """Base class for pluggable optimization policies."""

    def get_hold_time(
        self,
        train_id: str,
        block_id: str,
        current_time: float,
        simulator: "SmartRailSimulator",
    ) -> float:
        """Return hold time in minutes before train enters block. 0 = proceed."""
        return 0.0

    def get_delay_factor(self, train_id: str, block_id: str) -> float:
        """Return travel time multiplier. 1.0 = normal speed."""
        return 1.0


class GreedyPolicy(OptimizationPolicy):
    """
    Tier 1 — Priority-based greedy policy.
    Higher priority trains get precedence; lower priority trains hold.
    """

    def get_hold_time(
        self,
        train_id: str,
        block_id: str,
        current_time: float,
        simulator: "SmartRailSimulator",
    ) -> float:
        # Check if a higher-priority train is approaching same block
        for other_id, other_process in simulator.train_processes.items():
            if other_id == train_id:
                continue
            if other_process.current_block == block_id:
                my_priority = simulator.train_processes[train_id].config.priority
                their_priority = other_process.config.priority
                if their_priority > my_priority:
                    return 3.0  # hold 3 minutes
        return 0.0


class MILPPolicy(OptimizationPolicy):
    """
    Tier 2 — MILP-informed policy.
    Uses pre-computed schedule from OR-Tools solver to determine holds.
    """

    def __init__(self, schedule: Optional[dict] = None):
        self.schedule = schedule or {}
        # schedule format: {train_id: {block_id: entry_time}}

    def get_hold_time(
        self,
        train_id: str,
        block_id: str,
        current_time: float,
        simulator: "SmartRailSimulator",
    ) -> float:
        if train_id in self.schedule:
            planned_entry = self.schedule[train_id].get(block_id)
            if planned_entry and planned_entry > current_time:
                return planned_entry - current_time
        return 0.0


class RandomDelayPolicy(OptimizationPolicy):
    """
    Stress test policy — injects random delays to test robustness.
    Used for Schedule Stress Test simulation use case.
    """

    def __init__(self, delay_probability: float = 0.2, max_delay: float = 15.0):
        import random

        self.rng = random.Random(42)  # seeded for reproducibility
        self.delay_probability = delay_probability
        self.max_delay = max_delay
        self._injected: dict[str, float] = {}

    def get_delay_factor(self, train_id: str, block_id: str) -> float:
        key = f"{train_id}:{block_id}"
        if key not in self._injected:
            if self.rng.random() < self.delay_probability:
                self._injected[key] = 1.0 + self.rng.uniform(0.1, 0.5)
            else:
                self._injected[key] = 1.0
        return self._injected[key]


POLICY_REGISTRY: dict[str, type[OptimizationPolicy]] = {
    "greedy": GreedyPolicy,
    "milp": MILPPolicy,
    "random_delay": RandomDelayPolicy,
}


class SmartRailSimulator:
    """
    Main simulator class.

    Usage:
        sim = SmartRailSimulator(section_config, timetable, policy="greedy")
        metrics = sim.run(duration_minutes=1440)
    """

    def __init__(
        self,
        section_config: SectionConfig,
        timetable: list[TrainConfig],
        policy: str = "greedy",
        policy_kwargs: Optional[dict] = None,
        on_conflict: Optional[Callable] = None,
    ):
        self.section_config = section_config
        self.timetable = timetable
        self.on_conflict = on_conflict

        # SimPy environment
        self.env = simpy.Environment()

        # Track blocks as exclusive resources
        self.tracks: dict[str, simpy.Resource] = {
            block_id: simpy.PriorityResource(self.env, capacity=1)
            for block_id in section_config.blocks
        }

        # Optimization policy
        policy_class = POLICY_REGISTRY.get(policy, GreedyPolicy)
        self.policy: OptimizationPolicy = policy_class(**(policy_kwargs or {}))
        self.policy_name = policy

        # Train processes
        self.train_processes: dict[str, TrainProcess] = {}
        self._completed_trains: list[TrainProcess] = []

        # Delay injection registry
        self._delay_injections: dict[str, float] = {}
        self._delay_factors: dict[str, float] = {}

        # Conflict counter
        self.conflict_count = 0

        # Build train processes
        for config in timetable:
            process = TrainProcess(self.env, config, self.tracks, self)
            self.train_processes[config.train_id] = process

    def run(self, duration_minutes: int = 1440) -> SimulationMetrics:
        """
        Run simulation for duration_minutes of simulated time.
        Returns aggregated metrics.
        """
        wall_start = time.time()

        # Schedule all train processes
        for train_process in self.train_processes.values():
            self.env.process(train_process.run())

        # Run simulation
        self.env.run(until=duration_minutes)

        wall_time = time.time() - wall_start

        metrics = self._collect_metrics(wall_time)

        logger.info(
            f"Simulation complete: {metrics.completed_trains}/{metrics.total_trains} trains, "
            f"avg delay {metrics.average_delay_minutes:.1f}min, "
            f"wall time {wall_time:.2f}s"
        )

        return metrics

    def _collect_metrics(self, wall_time: float) -> SimulationMetrics:
        """Aggregate metrics from all completed train processes."""
        completed = self._completed_trains
        total = len(self.train_processes)

        if not completed:
            return SimulationMetrics(
                total_trains=total,
                simulation_wall_time_seconds=wall_time,
                policy_used=self.policy_name,
            )

        delays = [t.metrics.total_delay_minutes for t in completed]
        avg_delay = sum(delays) / len(delays)
        max_delay = max(delays)
        on_time = sum(1 for d in delays if d <= 5.0)
        punctuality = (on_time / len(completed)) * 100 if completed else 0

        # Throughput: trains completed per simulated hour
        sim_duration_hours = self.env.now / 60
        throughput = (
            len(completed) / sim_duration_hours if sim_duration_hours > 0 else 0
        )

        total_hold = sum(t.metrics.total_hold_time for t in completed)

        train_metrics = [
            {
                "train_id": t.config.train_id,
                "train_number": t.config.train_number,
                "priority": t.config.priority,
                "delay_minutes": t.metrics.total_delay_minutes,
                "actual_departure": t.metrics.actual_departure,
                "actual_arrival": t.metrics.actual_arrival,
                "blocks_traversed": t.metrics.blocks_traversed,
                "hold_time": t.metrics.total_hold_time,
                "status": t.status.value,
            }
            for t in completed
        ]

        return SimulationMetrics(
            total_trains=total,
            completed_trains=len(completed),
            average_delay_minutes=avg_delay,
            max_delay_minutes=max_delay,
            on_time_trains=on_time,
            punctuality_index=punctuality,
            total_conflicts_detected=self.conflict_count,
            total_hold_time_minutes=total_hold,
            throughput_per_hour=throughput,
            simulation_wall_time_seconds=wall_time,
            policy_used=self.policy_name,
            train_metrics=train_metrics,
        )

    def record_completion(self, train_process: TrainProcess):
        """Called by TrainProcess when journey completes."""
        self._completed_trains.append(train_process)

    def get_hold_time(self, train_id: str, block_id: str, current_time: float) -> float:
        """Delegate to policy."""
        return self.policy.get_hold_time(train_id, block_id, current_time, self)

    def get_delay_factor(self, train_id: str, block_id: str) -> float:
        """Delegate to policy."""
        return self.policy.get_delay_factor(train_id, block_id)

    def inject_train_delay(self, train_id: str, delay_minutes: float):
        """Inject delay for a specific train (used by what-if scenarios)."""
        self._delay_injections[train_id] = (
            self._delay_injections.get(train_id, 0) + delay_minutes
        )

    def inject_block_slowdown(self, block_id: str, factor: float):
        """Slow down all trains on a specific block (e.g. track fault)."""
        for train_id in self.train_processes:
            key = f"{train_id}:{block_id}"
            self._delay_factors[key] = factor

    def get_section_state(self) -> dict[str, Any]:
        """Snapshot of current simulation state."""
        return {
            "sim_time": self.env.now,
            "active_trains": [
                {
                    "train_id": tid,
                    "train_number": tp.config.train_number,
                    "status": tp.status.value,
                    "current_block": tp.current_block,
                    "current_delay": tp.current_delay,
                    "priority": tp.config.priority,
                }
                for tid, tp in self.train_processes.items()
                if tp.status != TrainStatus.COMPLETED
            ],
            "completed_count": len(self._completed_trains),
            "conflict_count": self.conflict_count,
        }
