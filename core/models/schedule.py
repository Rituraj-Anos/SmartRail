from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional


class ScheduleEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entry_id: str
    train_id: str
    station_id: Optional[str] = None
    block_id: Optional[str] = None
    planned_arrival: datetime
    planned_departure: datetime
    actual_arrival: Optional[datetime] = None
    actual_departure: Optional[datetime] = None
    status: str = "SCHEDULED"  # SCHEDULED, IN_TRANSIT, COMPLETED, DELAYED
    delay_minutes: float = 0.0


class OptimizationResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: str
    timestamp: datetime
    solve_time_ms: float
    objective_value: float
    is_optimal: bool
    conflicts_resolved: int
