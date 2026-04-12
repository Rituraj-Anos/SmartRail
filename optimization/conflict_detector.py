from enum import Enum
from pydantic import BaseModel, ConfigDict
from typing import List, Dict, Tuple, Set
from datetime import datetime

class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

class ConflictEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    event_id: str
    timestamp: datetime
    conflict_type: str
    train1_id: str
    train2_id: str
    block_id: str
    severity: Severity

class ConflictDetector:
    """
    Sweep-line algorithm over space-time schedules to detect conflicts.
    """
    def __init__(self, headway_minutes: int = 3):
        self.headway_minutes = headway_minutes

    def detect_conflicts(
        self, 
        schedules: Dict[str, List[Tuple[str, int]]]
    ) -> List[ConflictEvent]:
        
        events = []
        for train_id, path in schedules.items():
            for idx, (node_id, t) in enumerate(path):
                events.append((t, train_id, node_id))
                
        events.sort(key=lambda x: x[0])  # Sweep-line over time
        
        conflicts = []
        block_trains: Dict[str, List[Tuple[str, int]]] = {}
        
        # 1. Forward collision & Headway violation
        for t, train_id, node_id in events:
            if node_id not in block_trains:
                block_trains[node_id] = []
            
            for other_train, other_t in block_trains[node_id]:
                if other_train != train_id and abs(t - other_t) < self.headway_minutes:
                    conflict = ConflictEvent(
                        event_id=f"evt_{train_id}_{other_train}_{t}",
                        timestamp=datetime.now(),
                        conflict_type="HEADWAY_VIOLATION",
                        train1_id=train_id,
                        train2_id=other_train,
                        block_id=node_id,
                        severity=Severity.HIGH
                    )
                    conflicts.append(conflict)
                    
            block_trains[node_id].append((train_id, t))
            
        # 2. Deadlocks
        dependencies: Dict[str, Set[str]] = {t: set() for t in schedules.keys()}
        
        for t1 in schedules.keys():
            for t2 in schedules.keys():
                if t1 != t2:
                    p1 = [n for n, _ in schedules[t1]]
                    p2 = [n for n, _ in schedules[t2]]
                    common = list(set(p1) & set(p2))
                    if len(common) >= 2:
                        idx1_a = p1.index(common[0])
                        idx1_b = p1.index(common[1])
                        idx2_a = p2.index(common[0])
                        idx2_b = p2.index(common[1])
                        
                        if (idx1_a - idx1_b) * (idx2_a - idx2_b) < 0:
                            dependencies[t1].add(t2)
        
        visited = set()
        stack = set()
        
        def has_cycle(v: str) -> bool:
            visited.add(v)
            stack.add(v)
            for neighbor in dependencies[v]:
                if neighbor not in visited:
                    if has_cycle(neighbor):
                        return True
                elif neighbor in stack:
                    return True
            stack.remove(v)
            return False

        for t in dependencies.keys():
            if t not in visited:
                if has_cycle(t):
                    conflict = ConflictEvent(
                        event_id=f"deadlock_{t}",
                        timestamp=datetime.now(),
                        conflict_type="DEADLOCK",
                        train1_id=t,
                        train2_id="",
                        block_id="",
                        severity=Severity.HIGH
                    )
                    conflicts.append(conflict)
                    break 
                    
        return conflicts

    def detect_cascades(
        self,
        delayed_train_id: str,
        schedules: Dict[str, List[Tuple[str, int]]],
        delay_minutes: int
    ) -> List[str]:
        """BFS from delayed train to find all downstream affected trains."""
        affected = []
        delayed_blocks = {node for node, _ in schedules.get(delayed_train_id, [])}
        queue = [delayed_train_id]
        visited = {delayed_train_id}
        while queue:
            queue.pop(0)
            for other_id, path in schedules.items():
                if other_id in visited:
                    continue
                other_blocks = {node for node, _ in path}
                if delayed_blocks & other_blocks:
                    affected.append(other_id)
                    visited.add(other_id)
                    queue.append(other_id)
        return affected
