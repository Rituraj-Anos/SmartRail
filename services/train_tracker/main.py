"""
Train State Tracker Service
---------------------------
FastAPI service exposing real-time train state via REST and WebSocket.
Internal service — consumed by the Event Processor and Dashboard.
"""

import logging
import os

import redis
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from services.train_tracker.state import TrainState, TrainStateTracker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="SmartRail Train State Tracker",
    description="Real-time train state management service",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────
# Redis connection
# ─────────────────────────────────────────

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)
tracker = TrainStateTracker(redis_client)


# ─────────────────────────────────────────
# WebSocket connection manager
# ─────────────────────────────────────────


class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, message: dict):
        for ws in self.active.copy():
            try:
                await ws.send_json(message)
            except Exception:
                self.active.remove(ws)


manager = ConnectionManager()


# ─────────────────────────────────────────
# REST endpoints
# ─────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "service": "train_tracker", "redis": tracker.ping()}


@app.get("/sections/{section_id}/trains")
async def get_active_trains(section_id: str):
    """Return all active trains in a section."""
    trains = tracker.get_all_active_trains(section_id)
    return {"section_id": section_id, "trains": [t.to_dict() for t in trains]}


@app.get("/sections/{section_id}/summary")
async def get_section_summary(section_id: str):
    """Return KPI summary for a section."""
    return tracker.get_section_summary(section_id)


@app.get("/sections/{section_id}/delayed")
async def get_delayed_trains(section_id: str, min_delay_minutes: int = 5):
    """Return trains delayed beyond threshold."""
    trains = tracker.get_delayed_trains(section_id, min_delay_minutes * 60)
    return {"trains": [t.to_dict() for t in trains]}


@app.get("/trains/{train_id}")
async def get_train(train_id: str):
    """Return state of a single train."""
    state = tracker.get_train_state(train_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Train {train_id} not found")
    return state.to_dict()


@app.get("/trains/{train_id}/history")
async def get_train_history(train_id: str):
    """Return block transition history for a train."""
    return {"train_id": train_id, "history": tracker.get_train_history(train_id)}


@app.post("/trains/{train_id}/state")
async def upsert_train_state(train_id: str, state_data: dict):
    """Upsert a train's full state (called by Event Processor)."""
    try:
        state = TrainState.from_dict(state_data)
        tracker.update_train_state(state)
        # Broadcast update to all WebSocket clients
        await manager.broadcast({"event": "state_update", "data": state.to_dict()})
        return {"status": "ok", "train_id": train_id}
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.post("/trains/{train_id}/delay")
async def apply_delay(train_id: str, payload: dict):
    """Apply additional delay to a train."""
    additional_seconds = payload.get("additional_delay_seconds", 0)
    state = tracker.apply_delay_update(train_id, additional_seconds)
    if not state:
        raise HTTPException(status_code=404, detail=f"Train {train_id} not found")
    await manager.broadcast({"event": "delay_update", "data": state.to_dict()})
    return state.to_dict()


# ─────────────────────────────────────────
# WebSocket — real-time state streaming
# ─────────────────────────────────────────


@app.websocket("/ws/section/{section_id}")
async def websocket_section(websocket: WebSocket, section_id: str):
    """
    WebSocket endpoint — streams real-time train state updates for a section.
    On connect: sends current full state snapshot.
    On updates: broadcasts individual train state changes.
    """
    await manager.connect(websocket)
    try:
        # Send initial snapshot
        trains = tracker.get_all_active_trains(section_id)
        await websocket.send_json(
            {
                "event": "snapshot",
                "section_id": section_id,
                "data": [t.to_dict() for t in trains],
            }
        )
        # Keep connection alive
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info(f"WebSocket disconnected for section {section_id}")
