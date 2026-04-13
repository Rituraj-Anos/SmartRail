"""
Kafka Event Consumer
--------------------
Consumes events from all SmartRail Kafka topics and normalizes them
into internal event schema for processing by the orchestrator.

Topics:
  train.events       — position updates, speed changes, status changes
  disruption.alerts  — delays, breakdowns, emergency stops
  signal.events      — signal state changes (GREEN/YELLOW/RED)
  schedule.updates   — timetable modifications from TMS
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

from kafka import KafkaConsumer
from kafka.errors import KafkaError

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# Event schema
# ─────────────────────────────────────────


class EventType(str, Enum):
    # Train events
    POSITION_UPDATE = "position_update"
    SPEED_CHANGE = "speed_change"
    STATUS_CHANGE = "status_change"
    BLOCK_ENTRY = "block_entry"
    BLOCK_EXIT = "block_exit"
    # Disruption events
    DELAY_REPORTED = "delay_reported"
    BREAKDOWN = "breakdown"
    EMERGENCY_STOP = "emergency_stop"
    DELAY_RECOVERED = "delay_recovered"
    # Signal events
    SIGNAL_GREEN = "signal_green"
    SIGNAL_YELLOW = "signal_yellow"
    SIGNAL_RED = "signal_red"
    # Schedule events
    SCHEDULE_UPDATED = "schedule_updated"
    TRAIN_ADDED = "train_added"
    TRAIN_CANCELLED = "train_cancelled"


@dataclass
class SmartRailEvent:
    """Normalized internal event schema."""

    event_id: str
    event_type: str  # EventType value
    train_id: str
    train_number: str
    section_id: str
    timestamp: str  # ISO format UTC
    payload: dict  # event-specific data
    source_topic: str

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "train_id": self.train_id,
            "train_number": self.train_number,
            "section_id": self.section_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
            "source_topic": self.source_topic,
        }


# ─────────────────────────────────────────
# Topic definitions
# ─────────────────────────────────────────

KAFKA_TOPICS = {
    "train.events": [
        EventType.POSITION_UPDATE,
        EventType.SPEED_CHANGE,
        EventType.STATUS_CHANGE,
        EventType.BLOCK_ENTRY,
        EventType.BLOCK_EXIT,
    ],
    "disruption.alerts": [
        EventType.DELAY_REPORTED,
        EventType.BREAKDOWN,
        EventType.EMERGENCY_STOP,
        EventType.DELAY_RECOVERED,
    ],
    "signal.events": [
        EventType.SIGNAL_GREEN,
        EventType.SIGNAL_YELLOW,
        EventType.SIGNAL_RED,
    ],
    "schedule.updates": [
        EventType.SCHEDULE_UPDATED,
        EventType.TRAIN_ADDED,
        EventType.TRAIN_CANCELLED,
    ],
}

ALL_TOPICS = list(KAFKA_TOPICS.keys())


# ─────────────────────────────────────────
# Event normalizer
# ─────────────────────────────────────────


class EventNormalizer:
    """Normalizes raw Kafka messages into SmartRailEvent objects."""

    @staticmethod
    def normalize(raw_message: dict, topic: str) -> Optional[SmartRailEvent]:
        """
        Parse and validate a raw Kafka message.
        Returns None if message is malformed.
        """
        try:
            # Validate required fields
            required = [
                "event_id",
                "event_type",
                "train_id",
                "train_number",
                "section_id",
                "timestamp",
                "payload",
            ]
            for field in required:
                if field not in raw_message:
                    logger.warning(
                        f"Malformed event on topic {topic}: missing field '{field}'"
                    )
                    return None

            return SmartRailEvent(
                event_id=raw_message["event_id"],
                event_type=raw_message["event_type"],
                train_id=raw_message["train_id"],
                train_number=raw_message["train_number"],
                section_id=raw_message["section_id"],
                timestamp=raw_message["timestamp"],
                payload=raw_message["payload"],
                source_topic=topic,
            )
        except Exception as e:
            logger.error(f"Error normalizing event: {e}, raw={raw_message}")
            return None


# ─────────────────────────────────────────
# Kafka Consumer
# ─────────────────────────────────────────


class SmartRailKafkaConsumer:
    """
    Async-compatible Kafka consumer for all SmartRail topics.

    Usage:
        consumer = SmartRailKafkaConsumer(bootstrap_servers="kafka:9092")
        consumer.register_handler(EventType.DELAY_REPORTED, my_handler)
        await consumer.start()
    """

    def __init__(
        self,
        bootstrap_servers: Optional[str] = None,
        group_id: str = "smartrail-event-processor",
        topics: Optional[list[str]] = None,
    ):
        self.bootstrap_servers = bootstrap_servers or os.getenv(
            "KAFKA_BOOTSTRAP_SERVERS", "kafka:9092"
        )
        self.group_id = group_id
        self.topics = topics or ALL_TOPICS
        self.normalizer = EventNormalizer()
        self._handlers: dict[str, list[Callable]] = {}
        self._running = False
        self._consumer: Optional[KafkaConsumer] = None

        # Metrics
        self.messages_consumed = 0
        self.messages_failed = 0
        self.events_dispatched = 0

    def register_handler(
        self, event_type: EventType, handler: Callable[[SmartRailEvent], None]
    ) -> None:
        """Register a handler function for a specific event type."""
        self._handlers.setdefault(event_type.value, []).append(handler)
        logger.debug(f"Registered handler for event type: {event_type.value}")

    def register_catch_all_handler(
        self, handler: Callable[[SmartRailEvent], None]
    ) -> None:
        """Register a handler that receives ALL events regardless of type."""
        self._handlers.setdefault("*", []).append(handler)

    def _build_consumer(self) -> KafkaConsumer:
        return KafkaConsumer(
            *self.topics,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            auto_offset_reset="latest",
            enable_auto_commit=True,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
            consumer_timeout_ms=1000,  # poll timeout
            max_poll_records=100,
        )

    async def start(self) -> None:
        """Start consuming events. Runs in an async loop."""
        self._running = True
        logger.info(
            f"Starting Kafka consumer on {self.bootstrap_servers}, "
            f"topics={self.topics}, group={self.group_id}"
        )

        while self._running:
            try:
                if not self._consumer:
                    self._consumer = self._build_consumer()

                # Poll Kafka in a thread pool to avoid blocking async loop
                await asyncio.get_event_loop().run_in_executor(None, self._poll_batch)
                await asyncio.sleep(0.1)  # yield to event loop

            except KafkaError as e:
                logger.error(f"Kafka error: {e}. Reconnecting in 5s...")
                self._consumer = None
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Unexpected consumer error: {e}")
                await asyncio.sleep(1)

    def _poll_batch(self) -> None:
        """Poll one batch of messages from Kafka synchronously."""
        if not self._consumer:
            return

        try:
            for message in self._consumer:
                self.messages_consumed += 1
                topic = message.topic
                raw = message.value

                event = self.normalizer.normalize(raw, topic)
                if event:
                    self._dispatch(event)
                else:
                    self.messages_failed += 1

        except StopIteration:
            pass  # consumer_timeout_ms reached, no messages — normal

    def _dispatch(self, event: SmartRailEvent) -> None:
        """Dispatch a normalized event to all registered handlers."""
        handlers = self._handlers.get(event.event_type, [])
        handlers += self._handlers.get("*", [])  # catch-all handlers

        if not handlers:
            logger.debug(f"No handlers registered for event type: {event.event_type}")
            return

        for handler in handlers:
            try:
                handler(event)
                self.events_dispatched += 1
            except Exception as e:
                logger.error(
                    f"Handler error for event {event.event_id} "
                    f"(type={event.event_type}): {e}"
                )

    def stop(self) -> None:
        """Gracefully stop the consumer."""
        self._running = False
        if self._consumer:
            self._consumer.close()
            self._consumer = None
        logger.info("Kafka consumer stopped")

    def get_metrics(self) -> dict:
        return {
            "messages_consumed": self.messages_consumed,
            "messages_failed": self.messages_failed,
            "events_dispatched": self.events_dispatched,
            "topics": self.topics,
            "group_id": self.group_id,
        }


# ─────────────────────────────────────────
# Event factory (for testing + simulation)
# ─────────────────────────────────────────


class EventFactory:
    """Creates well-formed SmartRailEvent dicts for testing and simulation."""

    @staticmethod
    def delay_event(
        train_id: str,
        train_number: str,
        section_id: str,
        delay_seconds: int,
        block_id: str,
        reason: str = "unspecified",
    ) -> dict:
        return {
            "event_id": f"evt_{train_id}_{datetime.utcnow().timestamp():.0f}",
            "event_type": EventType.DELAY_REPORTED.value,
            "train_id": train_id,
            "train_number": train_number,
            "section_id": section_id,
            "timestamp": datetime.utcnow().isoformat(),
            "payload": {
                "delay_seconds": delay_seconds,
                "block_id": block_id,
                "reason": reason,
            },
        }

    @staticmethod
    def position_event(
        train_id: str,
        train_number: str,
        section_id: str,
        block_id: str,
        speed_kmh: float,
        direction: str,
    ) -> dict:
        return {
            "event_id": f"evt_{train_id}_{datetime.utcnow().timestamp():.0f}",
            "event_type": EventType.POSITION_UPDATE.value,
            "train_id": train_id,
            "train_number": train_number,
            "section_id": section_id,
            "timestamp": datetime.utcnow().isoformat(),
            "payload": {
                "block_id": block_id,
                "speed_kmh": speed_kmh,
                "direction": direction,
            },
        }

    @staticmethod
    def breakdown_event(
        train_id: str,
        train_number: str,
        section_id: str,
        block_id: str,
        estimated_recovery_minutes: int = 60,
    ) -> dict:
        return {
            "event_id": f"evt_{train_id}_{datetime.utcnow().timestamp():.0f}",
            "event_type": EventType.BREAKDOWN.value,
            "train_id": train_id,
            "train_number": train_number,
            "section_id": section_id,
            "timestamp": datetime.utcnow().isoformat(),
            "payload": {
                "block_id": block_id,
                "estimated_recovery_minutes": estimated_recovery_minutes,
            },
        }
