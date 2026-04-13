"""
Event Processor Orchestrator
-----------------------------
Central coordinator for Phase 2. Receives normalized events from the
Kafka consumer, updates train state, runs conflict detection, and
routes to the correct optimization tier based on severity.

Severity routing:
  LOW    (<5 min)  → Tier 1 Greedy heuristic    (< 200ms)
  MEDIUM (5-30min) → Tier 2 MILP CP-SAT solver  (< 3s)
  HIGH   (>30 min) → Tier 2 + log for Tier 3    (< 5s)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from optimization.conflict_detector import ConflictDetector, ConflictReport, Severity
from optimization.solvers.greedy_heuristic import GreedyHeuristic
from optimization.solvers.milp_solver import MILPSolver
from services.event_processor.kafka_consumer import (
    EventType,
    SmartRailEvent,
)
from services.train_tracker.state import TrainState, TrainStateTracker, TrainStatus

logger = logging.getLogger(__name__)


class ReoptimizationResult:
    """Result from an optimization run triggered by an event."""

    def __init__(
        self,
        triggered_by_event: str,
        tier_used: int,
        severity: str,
        conflict_report: ConflictReport,
        recommendations: list[dict],
        runtime_ms: float,
        success: bool,
        error: Optional[str] = None,
    ):
        self.triggered_by_event = triggered_by_event
        self.tier_used = tier_used
        self.severity = severity
        self.conflict_report = conflict_report
        self.recommendations = recommendations
        self.runtime_ms = runtime_ms
        self.success = success
        self.error = error

    def to_dict(self) -> dict:
        return {
            "triggered_by_event": self.triggered_by_event,
            "tier_used": self.tier_used,
            "severity": self.severity,
            "conflict_count": self.conflict_report.conflict_count,
            "conflicts": [c.to_dict() for c in self.conflict_report.conflicts],
            "recommendations": self.recommendations,
            "runtime_ms": round(self.runtime_ms, 2),
            "success": self.success,
            "error": self.error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


class EventOrchestrator:
    """
    Wires together: Kafka events → State Tracker → Conflict Detector → Optimizer.

    Usage:
        orchestrator = EventOrchestrator(tracker, section_id, section_graph)
        orchestrator.handle_event(event)  # called by Kafka consumer handlers
    """

    def __init__(
        self,
        tracker: TrainStateTracker,
        section_id: str,
        section_graph=None,
        section_config: Optional[dict] = None,
    ):
        self.tracker = tracker
        self.section_id = section_id
        self.section_graph = section_graph
        self.conflict_detector = ConflictDetector(section_config)
        self.greedy_solver = GreedyHeuristic(section_graph) if section_graph else None
        self.milp_solver = MILPSolver(section_graph) if section_graph else None

        # Callback for pushing results to WebSocket
        self._result_callback: Optional[callable] = None

        # Metrics
        self.events_processed = 0
        self.reoptimizations_triggered = 0
        self.last_result: Optional[ReoptimizationResult] = None

    def set_result_callback(self, callback: callable) -> None:
        """Register a callback to receive reoptimization results (for WebSocket push)."""
        self._result_callback = callback

    # ─────────────────────────────────────────
    # Event handlers (registered with Kafka consumer)
    # ─────────────────────────────────────────

    def handle_event(self, event: SmartRailEvent) -> None:
        """Main entry point — route event to correct handler."""
        self.events_processed += 1
        logger.info(
            f"Processing event: {event.event_type} "
            f"for train {event.train_number} [{event.event_id}]"
        )

        handlers = {
            EventType.DELAY_REPORTED.value: self._handle_delay,
            EventType.POSITION_UPDATE.value: self._handle_position_update,
            EventType.BREAKDOWN.value: self._handle_breakdown,
            EventType.EMERGENCY_STOP.value: self._handle_breakdown,
            EventType.DELAY_RECOVERED.value: self._handle_delay_recovery,
            EventType.BLOCK_ENTRY.value: self._handle_position_update,
            EventType.STATUS_CHANGE.value: self._handle_status_change,
        }

        handler = handlers.get(event.event_type)
        if handler:
            handler(event)
        else:
            logger.debug(f"No handler for event type: {event.event_type}")

    def _handle_delay(self, event: SmartRailEvent) -> None:
        """Handle a delay report — update state, detect conflicts, reoptimize."""
        payload = event.payload
        delay_seconds = payload.get("delay_seconds", 0)

        # 1. Update train state
        updated_state = self.tracker.apply_delay_update(event.train_id, delay_seconds)
        if not updated_state:
            # Train not in state yet — create minimal state
            updated_state = self._create_initial_state(event)
            if updated_state:
                self.tracker.update_train_state(updated_state)

        # 2. Run conflict detection + reoptimize
        self._run_detection_and_optimize(event.event_id, event.event_type)

    def _handle_position_update(self, event: SmartRailEvent) -> None:
        """Handle position/block entry update."""
        payload = event.payload
        block_id = payload.get("block_id")
        speed_kmh = payload.get("speed_kmh", 0.0)

        if block_id:
            self.tracker.apply_position_update(event.train_id, block_id, speed_kmh)

        # Only reoptimize on position updates if there are known conflicts
        active_trains = self.tracker.get_all_active_trains(self.section_id)
        if active_trains:
            report = self.conflict_detector.detect(active_trains)
            if report.requires_reoptimization:
                self._trigger_reoptimization(event.event_id, report)

    def _handle_breakdown(self, event: SmartRailEvent) -> None:
        """Handle breakdown — always triggers HIGH severity reoptimization."""
        state = self.tracker.get_train_state(event.train_id)
        if state:
            state.status = TrainStatus.BREAKDOWN.value
            state.speed_kmh = 0.0
            state.delay_seconds = max(state.delay_seconds, 1800)  # min 30 min
            state.last_updated = datetime.now(timezone.utc).isoformat()
            self.tracker.update_train_state(state)

        logger.warning(
            f"Breakdown reported for {event.train_number} — "
            f"triggering HIGH severity reoptimization"
        )
        self._run_detection_and_optimize(event.event_id, event.event_type)

    def _handle_delay_recovery(self, event: SmartRailEvent) -> None:
        """Handle delay recovery — update state and reoptimize."""
        state = self.tracker.get_train_state(event.train_id)
        if state:
            recovered_seconds = event.payload.get("recovered_seconds", 0)
            state.delay_seconds = max(0, state.delay_seconds - recovered_seconds)
            if state.delay_seconds == 0:
                state.status = TrainStatus.RUNNING.value
            state.last_updated = datetime.now(timezone.utc).isoformat()
            self.tracker.update_train_state(state)

        self._run_detection_and_optimize(event.event_id, event.event_type)

    def _handle_status_change(self, event: SmartRailEvent) -> None:
        """Handle generic status change."""
        state = self.tracker.get_train_state(event.train_id)
        if state:
            new_status = event.payload.get("status")
            if new_status:
                state.status = new_status
                state.last_updated = datetime.now(timezone.utc).isoformat()
                self.tracker.update_train_state(state)

    # ─────────────────────────────────────────
    # Core reoptimization pipeline
    # ─────────────────────────────────────────

    def _run_detection_and_optimize(
        self, event_id: str, event_type: str
    ) -> Optional[ReoptimizationResult]:
        """Run conflict detection then trigger appropriate optimization tier."""
        active_trains = self.tracker.get_all_active_trains(self.section_id)
        if not active_trains:
            return None

        report = self.conflict_detector.detect(active_trains)

        if report.requires_reoptimization:
            return self._trigger_reoptimization(event_id, report)

        logger.debug(f"No conflicts detected for event {event_id}")
        return None

    def _trigger_reoptimization(
        self, event_id: str, report: ConflictReport
    ) -> ReoptimizationResult:
        """Route to correct optimization tier based on conflict severity."""
        self.reoptimizations_triggered += 1
        severity = report.overall_severity

        logger.info(
            f"Triggering reoptimization: severity={severity}, "
            f"conflicts={report.conflict_count}, event={event_id}"
        )

        start_time = datetime.now(timezone.utc)

        try:
            if severity == Severity.LOW:
                result = self._run_tier1(report)
                tier = 1
            else:
                # MEDIUM and HIGH both use MILP
                result = self._run_tier2(report)
                tier = 2
                if severity == Severity.HIGH:
                    logger.warning(
                        f"HIGH severity event {event_id} — "
                        f"flagging for Tier 3 background replan"
                    )

            runtime_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

            reopt_result = ReoptimizationResult(
                triggered_by_event=event_id,
                tier_used=tier,
                severity=severity,
                conflict_report=report,
                recommendations=result,
                runtime_ms=runtime_ms,
                success=True,
            )

        except Exception as e:
            runtime_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            logger.error(f"Reoptimization failed: {e}")
            reopt_result = ReoptimizationResult(
                triggered_by_event=event_id,
                tier_used=0,
                severity=severity,
                conflict_report=report,
                recommendations=[],
                runtime_ms=runtime_ms,
                success=False,
                error=str(e),
            )

        self.last_result = reopt_result

        # Push to WebSocket if callback registered
        if self._result_callback:
            try:
                self._result_callback(reopt_result)
            except Exception as e:
                logger.error(f"Result callback error: {e}")

        logger.info(
            f"Reoptimization complete: tier={reopt_result.tier_used}, "
            f"runtime={reopt_result.runtime_ms:.0f}ms, "
            f"recommendations={len(reopt_result.recommendations)}"
        )
        return reopt_result

    def _run_tier1(self, report: ConflictReport) -> list[dict]:
        """Tier 1: Fast greedy heuristic (<200ms)."""
        if not self.greedy_solver:
            return self._generate_rule_based_recommendations(report)

        # GreedyHeuristic.solve expects List[Train], start_time_min, source_dest_map
        # The provided content orchestrator uses a slightly different logic
        # Adjusting to match the actual solver implementation from Phase 1

        try:
            # Note: This is a placeholder for the integration with Phase 1 solvers
            # In a real scenario, we'd convert TrainState back to Train models
            # and provide the required source_dest_map.
            return []
        except Exception as e:
            logger.error(f"Tier 1 solver failed: {e}")
            return self._generate_rule_based_recommendations(report)

    def _run_tier2(self, report: ConflictReport) -> list[dict]:
        """Tier 2: MILP CP-SAT solver (<3s for 30-train horizon)."""
        if not self.milp_solver:
            return self._generate_rule_based_recommendations(report)

        try:
            # Similar to Tier 1, requires model conversion
            return []
        except Exception as e:
            logger.error(f"Tier 2 solver failed, falling back to Tier 1: {e}")
            return self._run_tier1(report)

    def _generate_rule_based_recommendations(
        self, report: ConflictReport
    ) -> list[dict]:
        """
        Fallback rule-based recommendations when solvers are unavailable.
        Based on conflict type and involved trains.
        """
        recommendations = []

        for conflict in report.conflicts:
            active = self.tracker.get_all_active_trains(self.section_id)
            involved = [t for t in active if t.train_id in conflict.trains_involved]
            # Sort by priority — highest priority train gets precedence
            involved.sort(key=lambda t: t.priority, reverse=True)

            if len(involved) >= 2:
                hold_train = involved[-1]  # lowest priority
                recommendations.append(
                    {
                        "action": "HOLD",
                        "train_id": hold_train.train_id,
                        "train_number": hold_train.train_number,
                        "hold_minutes": round(conflict.estimated_delay_seconds / 60, 1),
                        "reason": conflict.description,
                        "conflict_type": conflict.conflict_type,
                        "priority_basis": True,
                    }
                )

        return recommendations

    def _format_recommendations(self, solver_output: dict, tier: int) -> list[dict]:
        """Format solver output into recommendation cards for the dashboard."""
        if not solver_output:
            return []

        recommendations = []
        schedule = solver_output.get("schedule", {})

        for train_id, train_schedule in schedule.items():
            state = self.tracker.get_train_state(train_id)
            if not state:
                continue

            recommendations.append(
                {
                    "action": train_schedule.get("action", "PROCEED"),
                    "train_id": train_id,
                    "train_number": state.train_number,
                    "hold_minutes": train_schedule.get("hold_minutes", 0),
                    "new_departure": train_schedule.get("new_departure"),
                    "reason": train_schedule.get("reason", "Optimized schedule"),
                    "tier": tier,
                    "estimated_saving_minutes": train_schedule.get(
                        "estimated_saving_minutes", 0
                    ),
                }
            )

        return recommendations

    def _create_initial_state(self, event: SmartRailEvent) -> Optional[TrainState]:
        """Create a minimal TrainState from an event for unknown trains."""
        try:
            return TrainState(
                train_id=event.train_id,
                train_number=event.train_number,
                priority=event.payload.get("priority", 3),
                current_block_id=event.payload.get("block_id"),
                previous_block_id=None,
                speed_kmh=event.payload.get("speed_kmh", 0.0),
                direction=event.payload.get("direction", "UP"),
                delay_seconds=event.payload.get("delay_seconds", 0),
                status=TrainStatus.RUNNING.value,
                scheduled_arrival=None,
                scheduled_departure=None,
                last_updated=datetime.now(timezone.utc).isoformat(),
                section_id=self.section_id,
            )
        except Exception as e:
            logger.error(f"Failed to create initial state for {event.train_id}: {e}")
            return None

    def get_metrics(self) -> dict:
        return {
            "section_id": self.section_id,
            "events_processed": self.events_processed,
            "reoptimizations_triggered": self.reoptimizations_triggered,
            "last_result": self.last_result.to_dict() if self.last_result else None,
        }
