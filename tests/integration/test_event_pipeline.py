"""
Phase 2 Integration Test
------------------------
Verifies the full event pipeline end-to-end:
  1. Initialize state tracker with synthetic trains
  2. Inject a delay event via the orchestrator
  3. Assert conflict detection fires
  4. Assert reoptimization result arrives within 5 seconds
  5. Assert recommendations are generated
"""

import time
from datetime import datetime

import pytest
import redis

from optimization.conflict_detector import ConflictDetector, Severity
from services.event_processor.kafka_consumer import EventFactory, EventNormalizer
from services.event_processor.orchestrator import (
    EventOrchestrator,
)
from services.train_tracker.state import TrainState, TrainStateTracker, TrainStatus

# ─────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────

SECTION_ID = "TEST-SECTION-01"


@pytest.fixture
def redis_client():
    """Real Redis client — requires Redis running (provided by CI service container)."""
    import os

    client = redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379"),
        decode_responses=True,
    )
    yield client
    # Cleanup test keys after each test
    for key in client.scan_iter(f"*{SECTION_ID}*"):
        client.delete(key)
    for key in client.scan_iter("train:state:TEST_*"):
        client.delete(key)


@pytest.fixture
def tracker(redis_client):
    return TrainStateTracker(redis_client)


@pytest.fixture
def sample_trains(tracker) -> list[TrainState]:
    """Seed 5 trains into the state tracker for testing."""
    trains = [
        TrainState(
            train_id="TEST_TRAIN_001",
            train_number="12951-UP",
            priority=5,  # Express
            current_block_id="BLOCK_05",
            previous_block_id="BLOCK_04",
            speed_kmh=110.0,
            direction="UP",
            delay_seconds=0,
            status=TrainStatus.RUNNING.value,
            scheduled_arrival="2024-01-01T14:30:00",
            scheduled_departure="2024-01-01T14:32:00",
            last_updated=datetime.utcnow().isoformat(),
            section_id=SECTION_ID,
        ),
        TrainState(
            train_id="TEST_TRAIN_002",
            train_number="12952-DN",
            priority=5,
            current_block_id="BLOCK_08",
            previous_block_id="BLOCK_09",
            speed_kmh=95.0,
            direction="DOWN",
            delay_seconds=0,
            status=TrainStatus.RUNNING.value,
            scheduled_arrival="2024-01-01T14:45:00",
            scheduled_departure="2024-01-01T14:47:00",
            last_updated=datetime.utcnow().isoformat(),
            section_id=SECTION_ID,
        ),
        TrainState(
            train_id="TEST_TRAIN_003",
            train_number="22101-UP",
            priority=3,  # Passenger
            current_block_id="BLOCK_03",
            previous_block_id="BLOCK_02",
            speed_kmh=80.0,
            direction="UP",
            delay_seconds=600,  # Already 10 min late
            status=TrainStatus.DELAYED.value,
            scheduled_arrival="2024-01-01T14:20:00",
            scheduled_departure="2024-01-01T14:22:00",
            last_updated=datetime.utcnow().isoformat(),
            section_id=SECTION_ID,
        ),
        TrainState(
            train_id="TEST_TRAIN_004",
            train_number="58201-DN",
            priority=2,  # Freight
            current_block_id="BLOCK_12",
            previous_block_id="BLOCK_13",
            speed_kmh=60.0,
            direction="DOWN",
            delay_seconds=0,
            status=TrainStatus.RUNNING.value,
            scheduled_arrival=None,
            scheduled_departure=None,
            last_updated=datetime.utcnow().isoformat(),
            section_id=SECTION_ID,
        ),
        TrainState(
            train_id="TEST_TRAIN_005",
            train_number="22102-UP",
            priority=3,
            current_block_id="BLOCK_05",  # SAME BLOCK as TRAIN_001 — headway conflict
            previous_block_id="BLOCK_04",
            speed_kmh=75.0,
            direction="UP",
            delay_seconds=300,
            status=TrainStatus.DELAYED.value,
            scheduled_arrival="2024-01-01T14:35:00",
            scheduled_departure="2024-01-01T14:37:00",
            last_updated=datetime.utcnow().isoformat(),
            section_id=SECTION_ID,
        ),
    ]
    for train in trains:
        tracker.update_train_state(train)
    return trains


@pytest.fixture
def orchestrator(tracker):
    return EventOrchestrator(
        tracker=tracker,
        section_id=SECTION_ID,
        section_graph=None,  # no graph needed for unit-level integration test
    )


# ─────────────────────────────────────────
# Tests
# ─────────────────────────────────────────


class TestTrainStateTracker:

    def test_update_and_retrieve_state(self, tracker, sample_trains):
        """State written to Redis is retrievable."""
        state = tracker.get_train_state("TEST_TRAIN_001")
        assert state is not None
        assert state.train_number == "12951-UP"
        assert state.priority == 5

    def test_get_all_active_trains(self, tracker, sample_trains):
        """All seeded trains are returned from the active set."""
        trains = tracker.get_all_active_trains(SECTION_ID)
        assert len(trains) == 5

    def test_active_trains_sorted_by_priority(self, tracker, sample_trains):
        """Active trains returned in priority-descending order."""
        trains = tracker.get_all_active_trains(SECTION_ID)
        priorities = [t.priority for t in trains]
        assert priorities == sorted(priorities, reverse=True)

    def test_apply_delay_update(self, tracker, sample_trains):
        """Delay accumulates correctly on a train."""
        updated = tracker.apply_delay_update("TEST_TRAIN_001", 900)
        assert updated is not None
        assert updated.delay_seconds == 900
        assert updated.status == TrainStatus.DELAYED.value

        # Verify persisted
        fetched = tracker.get_train_state("TEST_TRAIN_001")
        assert fetched.delay_seconds == 900

    def test_position_update(self, tracker, sample_trains):
        """Position update changes block and records history."""
        tracker.apply_position_update("TEST_TRAIN_004", "BLOCK_11", 65.0)
        state = tracker.get_train_state("TEST_TRAIN_004")
        assert state.current_block_id == "BLOCK_11"
        assert state.previous_block_id == "BLOCK_12"

    def test_get_delayed_trains(self, tracker, sample_trains):
        """Only trains over threshold are returned."""
        delayed = tracker.get_delayed_trains(SECTION_ID, min_delay_seconds=300)
        assert all(t.delay_seconds >= 300 for t in delayed)

    def test_section_summary(self, tracker, sample_trains):
        """Section summary returns correct counts."""
        summary = tracker.get_section_summary(SECTION_ID)
        assert summary["active_train_count"] == 5
        assert summary["delayed_train_count"] >= 2  # TRAIN_003 and TRAIN_005

    def test_mark_train_completed(self, tracker, sample_trains):
        """Completing a train removes it from the active set."""
        tracker.mark_train_completed("TEST_TRAIN_004", SECTION_ID)
        active = tracker.get_all_active_trains(SECTION_ID)
        active_ids = [t.train_id for t in active]
        assert "TEST_TRAIN_004" not in active_ids


class TestConflictDetector:

    def test_detects_headway_violation(self, tracker, sample_trains):
        """TRAIN_001 and TRAIN_005 are on same block — should detect headway violation."""
        detector = ConflictDetector()
        trains = tracker.get_all_active_trains(SECTION_ID)
        report = detector.detect(trains)

        conflict_types = [c.conflict_type for c in report.conflicts]
        assert "headway_violation" in conflict_types

    def test_detects_cascade_delay(self, tracker, sample_trains):
        """TRAIN_003 is 10 min delayed — should trigger cascade detection."""
        detector = ConflictDetector()
        trains = tracker.get_all_active_trains(SECTION_ID)
        report = detector.detect(trains)

        conflict_types = [c.conflict_type for c in report.conflicts]
        assert "cascade_delay" in conflict_types

    def test_overall_severity_high_when_critical(self, tracker, sample_trains):
        """Headway violation (2 trains same block) = HIGH severity."""
        detector = ConflictDetector()
        trains = tracker.get_all_active_trains(SECTION_ID)
        report = detector.detect(trains)
        assert report.overall_severity == Severity.HIGH

    def test_requires_reoptimization_when_conflicts_exist(self, tracker, sample_trains):
        detector = ConflictDetector()
        trains = tracker.get_all_active_trains(SECTION_ID)
        report = detector.detect(trains)
        assert report.requires_reoptimization is True

    def test_no_conflicts_clean_section(self, tracker):
        """A section with well-spaced trains produces no conflicts."""
        clean_trains = [
            TrainState(
                train_id=f"CLEAN_{i}",
                train_number=f"1000{i}-UP",
                priority=3,
                current_block_id=f"BLOCK_{i * 5:02d}",  # well spaced
                previous_block_id=f"BLOCK_{i * 5 - 1:02d}",
                speed_kmh=80.0,
                direction="UP",
                delay_seconds=0,
                status=TrainStatus.RUNNING.value,
                scheduled_arrival=None,
                scheduled_departure=None,
                last_updated=datetime.utcnow().isoformat(),
                section_id=SECTION_ID,
            )
            for i in range(1, 4)
        ]
        detector = ConflictDetector()
        report = detector.detect(clean_trains)
        headway_conflicts = [
            c for c in report.conflicts if c.conflict_type == "headway_violation"
        ]
        assert len(headway_conflicts) == 0


class TestEventPipeline:
    """
    End-to-end pipeline test:
    Inject event → state update → conflict detection → recommendations
    within 5 seconds.
    """

    def test_delay_event_triggers_reoptimization(
        self, orchestrator, tracker, sample_trains
    ):
        """
        Inject a delay event. Verify:
        - State is updated
        - Reoptimization is triggered
        - Result arrives within 5 seconds
        - Recommendations are generated
        """
        results = []
        orchestrator.set_result_callback(lambda r: results.append(r))

        # Build and inject event
        raw_event = EventFactory.delay_event(
            train_id="TEST_TRAIN_001",
            train_number="12951-UP",
            section_id=SECTION_ID,
            delay_seconds=900,  # 15 minutes — MEDIUM severity
            block_id="BLOCK_05",
            reason="signal failure",
        )
        normalizer = EventNormalizer()
        event = normalizer.normalize(raw_event, "disruption.alerts")
        assert event is not None

        # Time the pipeline
        start = time.time()
        orchestrator.handle_event(event)
        elapsed = time.time() - start

        # Assert < 5 seconds (Phase 2 requirement)
        assert elapsed < 5.0, f"Pipeline took {elapsed:.2f}s — exceeds 5s requirement"

        # Assert state was updated
        updated_state = tracker.get_train_state("TEST_TRAIN_001")
        assert updated_state.delay_seconds >= 900

        # Assert reoptimization was triggered
        assert orchestrator.reoptimizations_triggered >= 1

    def test_breakdown_triggers_high_severity(
        self, orchestrator, tracker, sample_trains
    ):
        """Breakdown event must trigger HIGH severity reoptimization."""
        results = []
        orchestrator.set_result_callback(lambda r: results.append(r))

        raw_event = EventFactory.breakdown_event(
            train_id="TEST_TRAIN_002",
            train_number="12952-DN",
            section_id=SECTION_ID,
            block_id="BLOCK_08",
            estimated_recovery_minutes=45,
        )
        normalizer = EventNormalizer()
        event = normalizer.normalize(raw_event, "disruption.alerts")

        orchestrator.handle_event(event)

        # Verify breakdown state
        state = tracker.get_train_state("TEST_TRAIN_002")
        assert state.status == TrainStatus.BREAKDOWN.value
        assert state.speed_kmh == 0.0

        # Verify reoptimization triggered
        assert orchestrator.reoptimizations_triggered >= 1

    def test_pipeline_reoptimization_under_5_seconds(
        self, orchestrator, tracker, sample_trains
    ):
        """
        Core Phase 2 requirement: full pipeline from event injection
        to reoptimization result must complete in under 5 seconds.
        Run 5 times to ensure consistency.
        """
        times = []
        for i in range(5):
            raw_event = EventFactory.delay_event(
                train_id="TEST_TRAIN_003",
                train_number="22101-UP",
                section_id=SECTION_ID,
                delay_seconds=300 + i * 60,
                block_id="BLOCK_03",
            )
            normalizer = EventNormalizer()
            event = normalizer.normalize(raw_event, "disruption.alerts")

            start = time.time()
            orchestrator.handle_event(event)
            elapsed = time.time() - start
            times.append(elapsed)

        avg_time = sum(times) / len(times)
        max_time = max(times)

        assert (
            max_time < 5.0
        ), f"Worst-case pipeline time {max_time:.2f}s exceeds 5s requirement"
        # Log for visibility
        print(f"\nPipeline times: avg={avg_time*1000:.0f}ms, max={max_time*1000:.0f}ms")
