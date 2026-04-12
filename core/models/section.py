from pydantic import BaseModel, ConfigDict
from typing import List


class Signal(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    signal_id: str
    location_km: float
    direction: str  # "UP" or "DOWN"


class TrackBlock(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    block_id: str
    start_km: float
    end_km: float
    length_km: float
    speed_limit_kmph: float
    is_electrified: bool = True
    gradient: float = 0.0


class LoopStation(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    station_id: str
    station_code: str
    name: str
    location_km: float
    number_of_loops: int
    loop_capacity_meters: float
    can_overtake: bool = True


class Section(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    section_id: str
    name: str
    start_station_code: str
    end_station_code: str
    total_length_km: float
    blocks: List[TrackBlock] = []
    stations: List[LoopStation] = []
    signals: List[Signal] = []
