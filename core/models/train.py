from enum import Enum
from pydantic import BaseModel, ConfigDict

class TrainType(str, Enum):
    PASSENGER = "passenger"
    EXPRESS = "express"
    FREIGHT = "freight"
    LOCAL = "local"
    SHATABDI = "shatabdi"
    RAJDHANI = "rajdhani"
    VANDE_BHARAT = "vande_bharat"

class Priority(int, Enum):
    CRITICAL = 5     # VIP, emergency, superfast
    HIGH = 4         # Express, passenger
    MEDIUM = 3       # Local
    LOW = 2          # Regular freight
    LOWEST = 1       # Empty rakes

class Train(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    train_id: str
    train_number: str
    train_type: TrainType
    priority: Priority
    max_speed_kmph: float
    length_meters: float
    acceleration_mps2: float = 0.5
    deceleration_mps2: float = 0.5
