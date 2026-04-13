"""
Train State Tracker
-------------------
Maintains a real-time in-memory + Redis-backed representation of every
train currently active in the section. Updated by the Event Processor
via Kafka events. Readable by the Conflict Detector and Dashboard.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

import redis

logger = logging.getLogger(__name__)


class TrainStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    DELAYED = "delayed"
    BREAKDOWN = "breakdown"
    COMPLETED = "completed"


class Direction(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


@dataclass
class TrainState:
    train_id: str
    train_number: str
    priority: int  # 5=Express, 4=Mail, 3=Passenger, 2=Freight
    current_block_id: Optional[str]
    previous_block_id: Optional[str]
    speed_kmh: float
    direction: str  # UP / DOWN
    delay_seconds: int
    status: str  # TrainStatus value
    scheduled_arrival: Optional[str]  # ISO format
    scheduled_departure: Optional[str]  # ISO format
    last_updated: str  # ISO format
    section_id: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TrainState":
        return cls(**data)

    @property
    def delay_minutes(self) -> float:
        return self.delay_seconds / 60.0

    @property
    def is_critically_delayed(self) -> bool:
        return self.delay_seconds >= 1800  # 30 minutes

    @property
    def priority_label(self) -> str:
        labels = {5: "Express", 4: "Mail", 3: "Passenger", 2: "Freight", 1: "Other"}
        return labels.get(self.priority, "Unknown")


class TrainStateTracker:
    """
    Redis-backed real-time state store for all active trains.

    Key schema:
      train:state:{train_id}        → JSON TrainState (TTL: 2 hours)
      section:active:{section_id}   → Redis Set of active train_ids
      train:history:{train_id}      → Redis List of last 50 block transitions
    """

    STATE_TTL_SECONDS = 7200  # 2 hours
    HISTORY_MAX_LENGTH = 50
    STATE_KEY_PREFIX = "train:state:"
    ACTIVE_KEY_PREFIX = "section:active:"
    HISTORY_KEY_PREFIX = "train:history:"

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    # ─────────────────────────────────────────
    # Write operations
    # ─────────────────────────────────────────

    def update_train_state(self, state: TrainState) -> None:
        """Upsert a train's full state into Redis."""
        key = f"{self.STATE_KEY_PREFIX}{state.train_id}"
        self.redis.setex(
            key,
            self.STATE_TTL_SECONDS,
            json.dumps(state.to_dict()),
        )
        # Track in section active set
        active_key = f"{self.ACTIVE_KEY_PREFIX}{state.section_id}"
        self.redis.sadd(active_key, state.train_id)
        self.redis.expire(active_key, self.STATE_TTL_SECONDS)

        # Append block transition to history if block changed
        if state.current_block_id:
            history_key = f"{self.HISTORY_KEY_PREFIX}{state.train_id}"
            entry = json.dumps(
                {
                    "block_id": state.current_block_id,
                    "timestamp": state.last_updated,
                    "speed_kmh": state.speed_kmh,
                    "delay_seconds": state.delay_seconds,
                }
            )
            self.redis.lpush(history_key, entry)
            self.redis.ltrim(history_key, 0, self.HISTORY_MAX_LENGTH - 1)
            self.redis.expire(history_key, self.STATE_TTL_SECONDS)

        logger.debug(
            f"Updated state for train {state.train_number} "
            f"(block={state.current_block_id}, delay={state.delay_minutes:.1f}min)"
        )

    def apply_delay_update(
        self, train_id: str, additional_delay_seconds: int
    ) -> Optional[TrainState]:
        """Add delay to an existing train state. Returns updated state."""
        state = self.get_train_state(train_id)
        if not state:
            logger.warning(f"Cannot apply delay: train {train_id} not found in state")
            return None

        state.delay_seconds += additional_delay_seconds
        state.status = TrainStatus.DELAYED.value
        state.last_updated = datetime.utcnow().isoformat()
        self.update_train_state(state)
        return state

    def apply_position_update(
        self,
        train_id: str,
        new_block_id: str,
        speed_kmh: float,
    ) -> Optional[TrainState]:
        """Update a train's position to a new block."""
        state = self.get_train_state(train_id)
        if not state:
            logger.warning(
                f"Cannot update position: train {train_id} not found in state"
            )
            return None

        state.previous_block_id = state.current_block_id
        state.current_block_id = new_block_id
        state.speed_kmh = speed_kmh
        state.last_updated = datetime.utcnow().isoformat()

        if speed_kmh > 0 and state.status == TrainStatus.STOPPED.value:
            state.status = TrainStatus.RUNNING.value

        self.update_train_state(state)
        return state

    def mark_train_completed(self, train_id: str, section_id: str) -> None:
        """Remove train from active set when it exits the section."""
        active_key = f"{self.ACTIVE_KEY_PREFIX}{section_id}"
        self.redis.srem(active_key, train_id)

        state = self.get_train_state(train_id)
        if state:
            state.status = TrainStatus.COMPLETED.value
            state.last_updated = datetime.utcnow().isoformat()
            self.update_train_state(state)

        logger.info(f"Train {train_id} marked as completed in section {section_id}")

    # ─────────────────────────────────────────
    # Read operations
    # ─────────────────────────────────────────

    def get_train_state(self, train_id: str) -> Optional[TrainState]:
        """Fetch a single train's state from Redis."""
        key = f"{self.STATE_KEY_PREFIX}{train_id}"
        raw = self.redis.get(key)
        if not raw:
            return None
        return TrainState.from_dict(json.loads(raw))

    def get_all_active_trains(self, section_id: str) -> list[TrainState]:
        """Return states of all active trains in a section."""
        active_key = f"{self.ACTIVE_KEY_PREFIX}{section_id}"
        train_ids = self.redis.smembers(active_key)

        states = []
        for train_id in train_ids:
            tid = train_id.decode() if isinstance(train_id, bytes) else train_id
            state = self.get_train_state(tid)
            if state:
                states.append(state)

        # Sort by priority descending, then by delay descending
        return sorted(states, key=lambda s: (-s.priority, -s.delay_seconds))

    def get_delayed_trains(
        self, section_id: str, min_delay_seconds: int = 300
    ) -> list[TrainState]:
        """Return trains delayed beyond threshold, sorted by delay descending."""
        all_trains = self.get_all_active_trains(section_id)
        return [t for t in all_trains if t.delay_seconds >= min_delay_seconds]

    def get_trains_in_block(self, block_id: str, section_id: str) -> list[TrainState]:
        """Return all trains currently occupying a given block."""
        all_trains = self.get_all_active_trains(section_id)
        return [t for t in all_trains if t.current_block_id == block_id]

    def get_train_history(self, train_id: str) -> list[dict]:
        """Return block transition history for a train (most recent first)."""
        history_key = f"{self.HISTORY_KEY_PREFIX}{train_id}"
        raw_entries = self.redis.lrange(history_key, 0, -1)
        return [json.loads(e) for e in raw_entries]

    def get_section_summary(self, section_id: str) -> dict:
        """Return a high-level summary of section state for KPI display."""
        all_trains = self.get_all_active_trains(section_id)
        delayed = [t for t in all_trains if t.delay_seconds > 0]

        avg_delay = (
            sum(t.delay_seconds for t in delayed) / len(delayed) if delayed else 0
        )

        return {
            "section_id": section_id,
            "active_train_count": len(all_trains),
            "delayed_train_count": len(delayed),
            "avg_delay_seconds": round(avg_delay, 1),
            "avg_delay_minutes": round(avg_delay / 60, 2),
            "critical_delays": len([t for t in all_trains if t.is_critically_delayed]),
            "breakdowns": len(
                [t for t in all_trains if t.status == TrainStatus.BREAKDOWN.value]
            ),
            "timestamp": datetime.utcnow().isoformat(),
        }

    # ─────────────────────────────────────────
    # Utility
    # ─────────────────────────────────────────

    def ping(self) -> bool:
        """Check Redis connectivity."""
        try:
            return self.redis.ping()
        except Exception:
            return False
