"""
Event Processor Service
-----------------------
Main FastAPI service for Phase 2. Wires together:
  - Kafka consumer (ingests events from all topics)
  - Train State Tracker (Redis-backed state)
  - Conflict Detector (sweep-line algorithm)
  - Orchestrator (routes to correct optimization tier)
  - WebSocket (pushes results to Controller Dashboard)
"""

import asyncio
import logging
import os

import redis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from services.event_processor.kafka_consumer import (
    SmartRailKafkaConsumer,
)
from services.event_processor.orchestrator import (
    EventOrchestrator,
    ReoptimizationResult,
)
from services.train_tracker.state import TrainStateTracker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="SmartRail Event Processor",
    description="Real-time event processing, conflict detection, and optimization service",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────
# Service configuration
# ─────────────────────────────────────────

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
DEFAULT_SECTION_ID = os.getenv("DEFAULT_SECTION_ID", "MUM-PUNE-01")

# ─────────────────────────────────────────
# WebSocket connection manager
# ─────────────────────────────────────────


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        logger.info(f"WebSocket connected. Total: {len(self.active)}")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        logger.info(f"WebSocket disconnected. Total: {len(self.active)}")

    async def broadcast(self, message: dict):
        disconnected = []
        for ws in self.active.copy():
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws)


manager = ConnectionManager()

# ─────────────────────────────────────────
# Service initialization
# ─────────────────────────────────────────

redis_client = redis.from_url(REDIS_URL, decode_responses=True)
state_tracker = TrainStateTracker(redis_client)
orchestrator = EventOrchestrator(
    tracker=state_tracker,
    section_id=DEFAULT_SECTION_ID,
)
kafka_consumer = SmartRailKafkaConsumer(
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
    group_id="smartrail-event-processor",
)


def on_reoptimization_result(result: ReoptimizationResult) -> None:
    """Callback — broadcast reoptimization results to all WebSocket clients."""
    asyncio.create_task(
        manager.broadcast(
            {
                "event": "reoptimization_result",
                "data": result.to_dict(),
            }
        )
    )


orchestrator.set_result_callback(on_reoptimization_result)


# Register Kafka handlers with orchestrator
kafka_consumer.register_catch_all_handler(orchestrator.handle_event)

# ─────────────────────────────────────────
# Startup / shutdown
# ─────────────────────────────────────────


@app.on_event("startup")
async def startup():
    """Start Kafka consumer as background task on service startup."""
    logger.info("Starting Event Processor service...")
    asyncio.create_task(kafka_consumer.start())
    logger.info(f"Kafka consumer started — topics: {kafka_consumer.topics}")


@app.on_event("shutdown")
async def shutdown():
    kafka_consumer.stop()
    logger.info("Event Processor service stopped")


# ─────────────────────────────────────────
# REST endpoints
# ─────────────────────────────────────────


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "event_processor",
        "redis": state_tracker.ping(),
        "kafka_topics": kafka_consumer.topics,
        "active_websockets": len(manager.active),
    }


@app.get("/metrics")
async def get_metrics():
    """Return service-level metrics."""
    return {
        "orchestrator": orchestrator.get_metrics(),
        "kafka": kafka_consumer.get_metrics(),
        "section_summary": state_tracker.get_section_summary(DEFAULT_SECTION_ID),
    }


@app.get("/sections/{section_id}/conflicts")
async def get_current_conflicts(section_id: str):
    """Run conflict detection on current state and return results."""
    from optimization.conflict_detector import ConflictDetector

    trains = state_tracker.get_all_active_trains(section_id)
    detector = ConflictDetector()
    report = detector.detect(trains)
    return report.to_dict()


@app.post("/simulate/delay")
async def simulate_delay(payload: dict):
    """
    Inject a simulated delay event for testing.
    Body: {train_id, train_number, section_id, delay_seconds, block_id}
    """
    from services.event_processor.kafka_consumer import EventFactory, EventNormalizer

    raw_event = EventFactory.delay_event(
        train_id=payload["train_id"],
        train_number=payload["train_number"],
        section_id=payload.get("section_id", DEFAULT_SECTION_ID),
        delay_seconds=payload["delay_seconds"],
        block_id=payload.get("block_id", "UNKNOWN"),
        reason=payload.get("reason", "simulated"),
    )
    normalizer = EventNormalizer()
    event = normalizer.normalize(raw_event, "disruption.alerts")
    if event:
        orchestrator.handle_event(event)
        return {"status": "injected", "event_id": raw_event["event_id"]}
    return {"status": "failed", "reason": "event normalization failed"}


@app.post("/simulate/breakdown")
async def simulate_breakdown(payload: dict):
    """Inject a simulated breakdown event for testing."""
    from services.event_processor.kafka_consumer import EventFactory, EventNormalizer

    raw_event = EventFactory.breakdown_event(
        train_id=payload["train_id"],
        train_number=payload["train_number"],
        section_id=payload.get("section_id", DEFAULT_SECTION_ID),
        block_id=payload.get("block_id", "UNKNOWN"),
        estimated_recovery_minutes=payload.get("estimated_recovery_minutes", 60),
    )
    normalizer = EventNormalizer()
    event = normalizer.normalize(raw_event, "disruption.alerts")
    if event:
        orchestrator.handle_event(event)
        return {"status": "injected", "event_id": raw_event["event_id"]}
    return {"status": "failed"}


# ─────────────────────────────────────────
# WebSocket — real-time results streaming
# ─────────────────────────────────────────


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """
    WebSocket endpoint — streams real-time reoptimization results
    and conflict alerts to the Controller Dashboard.
    """
    await manager.connect(websocket)
    try:
        # Send current section state as initial snapshot
        trains = state_tracker.get_all_active_trains(DEFAULT_SECTION_ID)
        await websocket.send_json(
            {
                "event": "snapshot",
                "data": {
                    "trains": [t.to_dict() for t in trains],
                    "summary": state_tracker.get_section_summary(DEFAULT_SECTION_ID),
                },
            }
        )
        # Keep connection alive — client can send pings
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"event": "pong"})
    except WebSocketDisconnect:
        manager.disconnect(websocket)
