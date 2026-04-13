from typing import List, Dict, Tuple
from core.models.train import Train, Priority
from core.models.section import Section
from core.graph.section_graph import SectionGraph


def get_priority_weight(priority: Priority) -> int:
    """Map Priority enum to weight (higher means more priority)."""
    return priority.value


class GreedyHeuristic:
    """
    Tier 1 Priority-based Greedy Solver.
    Input: Current section state (all train positions).
    Output: Conflict-free ordering decision.
    Target speed: < 200ms.
    """

    def __init__(self, section: Section):
        self.section = section
        self.section_graph = SectionGraph(section)

    def solve(
        self,
        trains: List[Train],
        start_time_min: int,
        source_dest_map: Dict[str, Tuple[str, str]],
    ) -> Dict[str, List[Tuple[str, int]]]:
        """
        source_dest_map: dict of train_id -> (start_node, dest_node)
        Returns: Dict of train_id -> path (list of (node, time))
        """
        # Sort trains by priority weight descending
        sorted_trains = sorted(
            trains, key=lambda t: get_priority_weight(t.priority), reverse=True
        )

        # Track intervals: block_id -> list of (start_time, end_time)
        blocked_intervals: Dict[str, List[Tuple[int, int]]] = {}
        schedules: Dict[str, List[Tuple[str, int]]] = {}

        for train in sorted_trains:
            if train.train_id not in source_dest_map:
                continue

            src, tgt = source_dest_map[train.train_id]

            # Since pathfinding considers `blocked_intervals`, the A* naturally avoids conflicts.
            path = self.section_graph.find_time_expanded_path(
                source=src,
                target=tgt,
                start_time_min=start_time_min,
                blocked_intervals=blocked_intervals,
            )

            if not path:
                # Unable to route, maybe everything is blocked
                schedules[train.train_id] = []
                continue

            schedules[train.train_id] = path

            # Formally reserve the blocks traversed by this train
            for i in range(len(path) - 1):
                u, t1 = path[i]
                v, t2 = path[i + 1]

                # Wait action
                if u == v:
                    continue

                edge_data = self.section_graph.graph.get_edge_data(u, v)
                if edge_data and "block_id" in edge_data:
                    block_id = edge_data["block_id"]
                    if block_id not in blocked_intervals:
                        blocked_intervals[block_id] = []
                    # Reserve block (exclusive)
                    blocked_intervals[block_id].append((t1, t2))

        return schedules
