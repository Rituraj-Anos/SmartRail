"""
SmartRail — Scenario Manager
Save, load, compare, and replay what-if simulation scenarios.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from simulator.engine import (
    SectionConfig,
    SimulationMetrics,
    SmartRailSimulator,
)
from simulator.train_process import TrainConfig

logger = logging.getLogger(__name__)

# Default scenarios storage directory
SCENARIOS_DIR = Path("simulator/scenarios")


@dataclass
class Scenario:
    """A saved what-if scenario with its configuration and results."""

    scenario_id: str
    name: str
    description: str
    created_at: str
    section_config: dict  # SectionConfig as dict
    timetable: list[dict]  # list of TrainConfig as dicts
    policy: str
    policy_kwargs: dict
    modifications: list[dict]  # list of injected changes
    metrics: Optional[dict] = None  # SimulationMetrics as dict (after run)
    tags: list[str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


class ScenarioManager:
    """
    Manages what-if simulation scenarios.

    Supports:
    - Creating scenarios from base configuration
    - Saving/loading to JSON files
    - Running scenarios and storing results
    - Comparing two scenarios side-by-side
    - Listing all saved scenarios
    """

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or SCENARIOS_DIR
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._active_scenarios: dict[str, Scenario] = {}

    def create_scenario(
        self,
        name: str,
        section_config: SectionConfig,
        timetable: list[TrainConfig],
        policy: str = "greedy",
        policy_kwargs: Optional[dict] = None,
        description: str = "",
        tags: Optional[list[str]] = None,
    ) -> Scenario:
        """Create a new scenario (not yet run)."""
        scenario_id = str(uuid.uuid4())[:8]

        scenario = Scenario(
            scenario_id=scenario_id,
            name=name,
            description=description,
            created_at=datetime.now(timezone.utc).isoformat(),
            section_config=self._section_config_to_dict(section_config),
            timetable=[self._train_config_to_dict(t) for t in timetable],
            policy=policy,
            policy_kwargs=policy_kwargs or {},
            modifications=[],
            tags=tags or [],
        )

        self._active_scenarios[scenario_id] = scenario
        logger.info(f"Created scenario '{name}' (id={scenario_id})")
        return scenario

    def add_delay_modification(
        self,
        scenario: Scenario,
        train_id: str,
        delay_minutes: float,
        reason: str = "",
    ) -> Scenario:
        """Add a delay injection to an existing scenario."""
        scenario.modifications.append(
            {
                "type": "delay",
                "train_id": train_id,
                "delay_minutes": delay_minutes,
                "reason": reason,
            }
        )
        return scenario

    def add_block_slowdown(
        self,
        scenario: Scenario,
        block_id: str,
        factor: float,
        reason: str = "",
    ) -> Scenario:
        """Add a block speed reduction (e.g. track fault)."""
        scenario.modifications.append(
            {
                "type": "block_slowdown",
                "block_id": block_id,
                "factor": factor,
                "reason": reason,
            }
        )
        return scenario

    def add_breakdown(
        self,
        scenario: Scenario,
        train_id: str,
        at_block: str,
        duration_minutes: float,
    ) -> Scenario:
        """Simulate a train breakdown at a specific block."""
        scenario.modifications.append(
            {
                "type": "breakdown",
                "train_id": train_id,
                "block_id": at_block,
                "duration_minutes": duration_minutes,
            }
        )
        return scenario

    def run_scenario(
        self,
        scenario: Scenario,
        duration_minutes: int = 1440,
    ) -> SimulationMetrics:
        """Execute a scenario and store results."""
        # Rebuild objects from dicts
        section_config = self._dict_to_section_config(scenario.section_config)
        timetable = [self._dict_to_train_config(t) for t in scenario.timetable]

        # Build simulator
        sim = SmartRailSimulator(
            section_config=section_config,
            timetable=timetable,
            policy=scenario.policy,
            policy_kwargs=scenario.policy_kwargs,
        )

        # Apply modifications
        for mod in scenario.modifications:
            if mod["type"] == "delay":
                sim.inject_train_delay(mod["train_id"], mod["delay_minutes"])
            elif mod["type"] == "block_slowdown":
                sim.inject_block_slowdown(mod["block_id"], mod["factor"])
            elif mod["type"] == "breakdown":
                sim.inject_train_delay(mod["train_id"], mod["duration_minutes"])

        # Run
        metrics = sim.run(duration_minutes=duration_minutes)

        # Store results
        scenario.metrics = self._metrics_to_dict(metrics)
        self._active_scenarios[scenario.scenario_id] = scenario

        logger.info(
            f"Scenario '{scenario.name}' completed: "
            f"avg_delay={metrics.average_delay_minutes:.1f}min, "
            f"punctuality={metrics.punctuality_index:.1f}%"
        )

        return metrics

    def save_scenario(self, scenario: Scenario) -> Path:
        """Persist scenario to JSON file."""
        file_path = self.storage_dir / f"{scenario.scenario_id}.json"
        with open(file_path, "w") as f:
            json.dump(asdict(scenario), f, indent=2)
        logger.info(f"Saved scenario '{scenario.name}' to {file_path}")
        return file_path

    def load_scenario(self, scenario_id: str) -> Scenario:
        """Load scenario from JSON file."""
        file_path = self.storage_dir / f"{scenario_id}.json"
        if not file_path.exists():
            raise FileNotFoundError(f"Scenario {scenario_id} not found")

        with open(file_path) as f:
            data = json.load(f)

        scenario = Scenario(**data)
        self._active_scenarios[scenario_id] = scenario
        return scenario

    def list_scenarios(self) -> list[dict[str, Any]]:
        """List all saved scenarios with summary."""
        scenarios = []
        for json_file in self.storage_dir.glob("*.json"):
            with open(json_file) as f:
                data = json.load(f)
            scenarios.append(
                {
                    "scenario_id": data["scenario_id"],
                    "name": data["name"],
                    "description": data["description"],
                    "created_at": data["created_at"],
                    "policy": data["policy"],
                    "tags": data.get("tags", []),
                    "has_results": data.get("metrics") is not None,
                    "avg_delay": (
                        data["metrics"].get("average_delay_minutes")
                        if data.get("metrics")
                        else None
                    ),
                }
            )
        return sorted(scenarios, key=lambda x: x["created_at"], reverse=True)

    def compare_scenarios(
        self,
        scenario_a_id: str,
        scenario_b_id: str,
    ) -> dict[str, Any]:
        """
        Compare two scenarios side-by-side.
        Returns delta metrics — positive means B is worse than A.
        """
        a = self._get_or_load(scenario_a_id)
        b = self._get_or_load(scenario_b_id)

        if not a.metrics or not b.metrics:
            raise ValueError(
                "Both scenarios must be run before comparison. "
                f"A has results: {a.metrics is not None}, "
                f"B has results: {b.metrics is not None}"
            )

        def delta(key: str) -> Optional[float]:
            va = a.metrics.get(key)
            vb = b.metrics.get(key)
            if va is not None and vb is not None:
                return round(vb - va, 3)
            return None

        return {
            "scenario_a": {
                "id": a.scenario_id,
                "name": a.name,
                "policy": a.policy,
                "metrics": a.metrics,
            },
            "scenario_b": {
                "id": b.scenario_id,
                "name": b.name,
                "policy": b.policy,
                "metrics": b.metrics,
            },
            "delta": {
                "average_delay_minutes": delta("average_delay_minutes"),
                "max_delay_minutes": delta("max_delay_minutes"),
                "punctuality_index": delta("punctuality_index"),
                "throughput_per_hour": delta("throughput_per_hour"),
                "total_hold_time_minutes": delta("total_hold_time_minutes"),
                "completed_trains": delta("completed_trains"),
            },
            "summary": self._generate_comparison_summary(a, b),
        }

    def _generate_comparison_summary(self, a: Scenario, b: Scenario) -> str:
        """Generate human-readable comparison summary."""
        if not a.metrics or not b.metrics:
            return "Cannot compare — missing results"

        delay_a = a.metrics.get("average_delay_minutes", 0)
        delay_b = b.metrics.get("average_delay_minutes", 0)
        punct_a = a.metrics.get("punctuality_index", 0)
        punct_b = b.metrics.get("punctuality_index", 0)

        if delay_b < delay_a:
            delay_verdict = (
                f"'{b.name}' reduces avg delay by "
                f"{delay_a - delay_b:.1f} minutes vs '{a.name}'"
            )
        elif delay_b > delay_a:
            delay_verdict = (
                f"'{b.name}' increases avg delay by "
                f"{delay_b - delay_a:.1f} minutes vs '{a.name}'"
            )
        else:
            delay_verdict = "Both scenarios have equal average delay"

        punct_verdict = f"Punctuality: {punct_a:.1f}% vs {punct_b:.1f}%"

        return f"{delay_verdict}. {punct_verdict}."

    # ── Serialization helpers ──────────────────────────────────────────────

    @staticmethod
    def _section_config_to_dict(config: SectionConfig) -> dict:
        return {
            "section_id": config.section_id,
            "section_name": config.section_name,
            "blocks": config.blocks,
            "stations": config.stations,
            "total_length_km": config.total_length_km,
            "is_single_line": config.is_single_line,
        }

    @staticmethod
    def _dict_to_section_config(d: dict) -> SectionConfig:
        return SectionConfig(**d)

    @staticmethod
    def _train_config_to_dict(config: TrainConfig) -> dict:
        return {
            "train_id": config.train_id,
            "train_number": config.train_number,
            "priority": config.priority,
            "route": config.route,
            "scheduled_departure": config.scheduled_departure,
            "scheduled_arrival": config.scheduled_arrival,
            "max_speed_kmh": config.max_speed_kmh,
            "base_travel_times": config.base_travel_times,
        }

    @staticmethod
    def _dict_to_train_config(d: dict) -> TrainConfig:
        return TrainConfig(**d)

    @staticmethod
    def _metrics_to_dict(metrics: SimulationMetrics) -> dict:
        return asdict(metrics)

    def _get_or_load(self, scenario_id: str) -> Scenario:
        if scenario_id in self._active_scenarios:
            return self._active_scenarios[scenario_id]
        return self.load_scenario(scenario_id)
