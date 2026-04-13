"""
Optimization Engine core module and Solver Factory.
"""

from typing import Dict, List, Tuple
from core.models.train import Train
from core.models.section import Section
from optimization.solvers.greedy_heuristic import GreedyHeuristic
from optimization.solvers.milp_solver import MILPSolver
from optimization.conflict_detector import ConflictDetector


class DisruptionSeverity:
    MILD = "MILD"
    MODERATE = "MODERATE"
    SEVERE = "SEVERE"


class SolverFactory:
    """
    Dynamically select optimization strategies based on disruption severity.
    Tier 1 (Greedy Heuristic): For Mild or Severe (fallback) disruptions.
    Tier 2 (MILP Solver): For Moderate disruptions requiring global optimization.
    """

    def __init__(self, section: Section):
        self.section = section

    def solve(
        self,
        trains: List[Train],
        start_time_min: int,
        source_dest_map: Dict[str, Tuple[str, str]],
        disruption_severity: str = DisruptionSeverity.MILD,
    ) -> Dict[str, List[Tuple[str, int]]]:

        if disruption_severity == DisruptionSeverity.MODERATE:
            # Use Tier 2 MILP with a 60 min horizon target and 3.0s timeout
            solver = MILPSolver(self.section, horizon_minutes=60, timeout_sec=3.0)
            schedules = solver.solve(trains, start_time_min, source_dest_map)

            # Fallback to greedy if MILP doesn't find a solution in time
            if not schedules:
                greedy_heuristic = GreedyHeuristic(self.section)
                schedules = greedy_heuristic.solve(
                    trains, start_time_min, source_dest_map
                )
            return schedules
        else:
            # Tier 1 Greedy for fast execution in mild/severe situations
            greedy_heuristic = GreedyHeuristic(self.section)
            return greedy_heuristic.solve(trains, start_time_min, source_dest_map)


__all__ = [
    "GreedyHeuristic",
    "MILPSolver",
    "ConflictDetector",
    "SolverFactory",
    "DisruptionSeverity",
]
