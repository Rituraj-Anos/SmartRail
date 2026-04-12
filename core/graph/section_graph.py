import networkx as nx
from typing import Dict, List, Optional, Tuple, Set
import heapq
from core.models.section import Section

class SectionGraph:
    """
    NetworkX-based section topology for conflict detection and routing.
    Extended with Time-Expanded graph concepts for A* routing over time.
    """
    def __init__(self, section: Section):
        self.section = section
        self.graph = nx.DiGraph()
        self._build_graph()

    def _build_graph(self) -> None:
        """
        Construct the graph where nodes are stations/signals and edges are track blocks.
        """
        for station in self.section.stations:
            self.graph.add_node(
                station.station_code, 
                type="station", 
                location=station.location_km,
                capacity=station.number_of_loops
            )
            
        sorted_blocks = sorted(self.section.blocks, key=lambda b: b.start_km)
        
        for block in sorted_blocks:
            start_node = self._find_closest_node(block.start_km)
            end_node = self._find_closest_node(block.end_km)
            
            if start_node and end_node and start_node != end_node:
                travel_time_min = (block.length_km / block.speed_limit_kmph) * 60.0
                travel_min = int(travel_time_min) if int(travel_time_min) > 0 else 1
                
                self.graph.add_edge(
                    start_node, 
                    end_node, 
                    block_id=block.block_id,
                    length_km=block.length_km,
                    speed_limit=block.speed_limit_kmph,
                    weight=travel_time_min,
                    travel_minutes=travel_min
                )
                self.graph.add_edge(
                    end_node, 
                    start_node, 
                    block_id=block.block_id,
                    length_km=block.length_km,
                    speed_limit=block.speed_limit_kmph,
                    weight=travel_time_min,
                    travel_minutes=travel_min
                )

    def _find_closest_node(self, location_km: float) -> Optional[str]:
        closest_node = None
        min_dist = float('inf')
        for node, data in self.graph.nodes(data=True):
            dist = abs(data['location'] - location_km)
            if dist < min_dist and dist <= 1.0:
                min_dist = dist
                closest_node = node
        return closest_node

    def get_shortest_path(self, source: str, target: str) -> List[str]:
        try:
            return nx.shortest_path(self.graph, source=source, target=target, weight='weight')
        except nx.NetworkXNoPath:
            return []

    def find_time_expanded_path(
        self, 
        source: str, 
        target: str, 
        start_time_min: int, 
        blocked_intervals: Dict[str, List[Tuple[int, int]]]
    ) -> List[Tuple[str, int]]:
        """
        A* search over a time-expanded grid.
        Returns a path as a list of (node, arrival_time).
        blocked_intervals: dict mapping block_id to a list of (start_t, end_t) reservations.
        """
        def heuristic(node: str) -> float:
            try:
                return nx.shortest_path_length(self.graph, source=node, target=target, weight='weight')
            except nx.NetworkXNoPath:
                return float('inf')
        
        open_set: List[Tuple[float, int, str, List[Tuple[str, int]]]] = []
        heapq.heappush(open_set, (heuristic(source), start_time_min, source, [(source, start_time_min)]))
        
        visited: Set[Tuple[str, int]] = set()
        
        while open_set:
            f, current_time, current_node, path = heapq.heappop(open_set)
            
            if current_node == target:
                return path
                
            state = (current_node, current_time)
            if state in visited:
                continue
            visited.add(state)
            
            # Action 1: Wait 1 minute
            wait_time = current_time + 1
            if (current_node, wait_time) not in visited:
                heapq.heappush(open_set, (wait_time + heuristic(current_node), wait_time, current_node, path + [(current_node, wait_time)]))
                
            # Action 2: Traverse edge
            for neighbor in self.graph.successors(current_node):
                edge_data = self.graph.edges[current_node, neighbor]
                travel_time = edge_data.get('travel_minutes', 1)
                block_id = edge_data.get('block_id')
                arrival_time = current_time + travel_time
                
                block_available = True
                if block_id in blocked_intervals:
                    for (b_start, b_end) in blocked_intervals[block_id]:
                        if not (arrival_time <= b_start or current_time >= b_end):
                            block_available = False
                            break
                            
                if block_available:
                    if (neighbor, arrival_time) not in visited:
                        h = heuristic(neighbor)
                        if h != float('inf'):
                            heapq.heappush(open_set, (arrival_time + h, arrival_time, neighbor, path + [(neighbor, arrival_time)]))
                            
        return []
