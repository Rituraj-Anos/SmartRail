"""
SmartRail — Train Process
SimPy process modeling individual train movement through track blocks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional

import simpy

if TYPE_CHECKING:
    from simulator.engine import SmartRailSimulator

logger = logging.getLogger(__name__)


class TrainStatus(Enum):
    WAITING = "waiting"
    MOVING = "moving"
    HOLDING = "holding"
    COMPLETED = "completed"
    BROKEN_DOWN = "broken_down"


@dataclass
class TrainMetrics:
    train_id: str
    scheduled_departure: float
    actual_departure: Optional[float] = None
    scheduled_arrival: float = 0.0
    actual_arrival: Optional[float] = None
    total_delay_minutes: float = 0.0
    blocks_traversed: int = 0
    total_hold_time: float = 0.0
    status: TrainStatus = TrainStatus.WAITING


@dataclass
class TrainConfig:
    train_id: str
    train_number: str
    priority: int  # 5=Express, 4=Mail, 3=Passenger, 2=Freight
    route: list[str]  # ordered list of block/station IDs
    scheduled_departure: float  # minutes from sim start
    scheduled_arrival: float  # minutes from sim start
    max_speed_kmh: float = 100.0
    base_travel_times: dict[str, float] = field(default_factory=dict)
    # block_id -> travel time in minutes


class TrainProcess:
    """
    SimPy process modeling a train moving through a sequence of track blocks.
    Each block is a simpy.Resource(capacity=1) — exclusive occupancy.
    """

    def __init__(
        self,
        env: simpy.Environment,
        config: TrainConfig,
        tracks: dict[str, simpy.Resource],
        simulator: "SmartRailSimulator",
    ):
        self.env = env
        self.config = config
        self.tracks = tracks
        self.simulator = simulator
        self.metrics = TrainMetrics(
            train_id=config.train_id,
            scheduled_departure=config.scheduled_departure,
            scheduled_arrival=config.scheduled_arrival,
        )
        self.status = TrainStatus.WAITING
        self.current_block: Optional[str] = None
        self._process: Optional[simpy.Process] = None

    def run(self):
        """Main SimPy generator — models full train journey."""
        # Wait until scheduled departure
        departure_wait = max(0, self.config.scheduled_departure - self.env.now)
        if departure_wait > 0:
            yield self.env.timeout(departure_wait)

        self.metrics.actual_departure = self.env.now
        self.status = TrainStatus.MOVING

        logger.debug(
            f"[t={self.env.now:.1f}] Train {self.config.train_number} departed"
        )

        # Traverse each block in route
        for block_id in self.config.route:
            yield self.env.process(self._traverse_block(block_id))

        # Journey complete
        self.metrics.actual_arrival = self.env.now
        self.metrics.total_delay_minutes = max(
            0, self.env.now - self.config.scheduled_arrival
        )
        self.status = TrainStatus.COMPLETED

        # Record in simulator
        self.simulator.record_completion(self)

        logger.debug(
            f"[t={self.env.now:.1f}] Train {self.config.train_number} arrived. "
            f"Delay: {self.metrics.total_delay_minutes:.1f}min"
        )

    def _traverse_block(self, block_id: str):
        """Request block, travel, release block."""
        if block_id not in self.tracks:
            logger.warning(f"Block {block_id} not in track map, skipping")
            return

        track_resource = self.tracks[block_id]
        request_time = self.env.now

        # Request exclusive access to block
        with track_resource.request(
            priority=-self.config.priority  # higher priority = lower number = served first
        ) as req:
            # Apply policy-based hold if needed
            hold_time = self.simulator.get_hold_time(
                self.config.train_id, block_id, self.env.now
            )
            if hold_time > 0:
                self.status = TrainStatus.HOLDING
                yield self.env.timeout(hold_time)
                self.metrics.total_hold_time += hold_time

            self.status = TrainStatus.MOVING
            yield req

            wait_time = self.env.now - request_time - hold_time
            if wait_time > 0.1:
                logger.debug(
                    f"Train {self.config.train_number} waited {wait_time:.1f}min "
                    f"for block {block_id}"
                )

            self.current_block = block_id

            # Travel through block
            travel_time = self._get_travel_time(block_id)
            yield self.env.timeout(travel_time)

            self.metrics.blocks_traversed += 1
            self.current_block = None

    def _get_travel_time(self, block_id: str) -> float:
        """Get travel time for block, with delay injection support."""
        base_time = self.config.base_travel_times.get(block_id, 5.0)

        # Apply any injected delays from simulator
        delay_factor = self.simulator.get_delay_factor(self.config.train_id, block_id)
        return base_time * delay_factor

    def inject_delay(self, additional_minutes: float):
        """Inject a delay into current or next block traversal."""
        self.simulator.inject_train_delay(self.config.train_id, additional_minutes)
        logger.info(
            f"Delay of {additional_minutes}min injected into "
            f"train {self.config.train_number}"
        )

    @property
    def current_delay(self) -> float:
        """Estimate current delay based on sim time vs schedule."""
        if self.status == TrainStatus.COMPLETED:
            return self.metrics.total_delay_minutes
        if self.metrics.actual_departure is None:
            return max(0, self.env.now - self.config.scheduled_departure)
        return max(0, self.env.now - self.config.scheduled_arrival)
