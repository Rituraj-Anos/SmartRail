from ortools.sat.python import cp_model
from typing import Dict, List, Tuple
from core.models.train import Train
from core.models.section import Section
from optimization.solvers.greedy_heuristic import get_priority_weight


class MILPSolver:
    """
    Tier 2 MILP Solver using Google OR-Tools CP-SAT.
    Solves train scheduling over a rolling horizon window.
    """

    def __init__(
        self, section: Section, horizon_minutes: int = 60, timeout_sec: float = 3.0
    ):
        self.section = section
        self.horizon_minutes = horizon_minutes
        self.timeout_sec = timeout_sec
        self.min_headway_min = 3

    def solve(
        self,
        trains: List[Train],
        start_time_min: int,
        source_dest_map: Dict[str, Tuple[str, str]],
    ) -> Dict[str, List[Tuple[str, int]]]:

        model = cp_model.CpModel()

        T = range(self.horizon_minutes)
        # Combine track blocks and stations
        B_track = [b.block_id for b in self.section.blocks]
        B_stations = [s.station_code for s in self.section.stations]
        B = B_track + B_stations

        # Retrieve capacities
        capacities = {b: 1 for b in B_track}
        for s in self.section.stations:
            capacities[s.station_code] = s.number_of_loops

        # Variables: x[i][s][t]
        x: Dict[Tuple[str, str, int], cp_model.IntVar] = {}
        for train in trains:
            i = train.train_id
            for s in B:
                for t in T:
                    x[(i, s, t)] = model.NewBoolVar(f"x_{i}_{s}_{t}")

        # Variables: y[i][j]
        y: Dict[Tuple[str, str], cp_model.IntVar] = {}
        for idx, t1 in enumerate(trains):
            for t2 in trains[idx + 1 :]:
                i = t1.train_id
                j = t2.train_id
                y[(i, j)] = model.NewBoolVar(f"y_{i}_{j}")
                y[(j, i)] = model.NewBoolVar(f"y_{j}_{i}")
                # Crossing precedence constraint
                model.Add(y[(i, j)] + y[(j, i)] == 1)

        # Variables: d[i]
        d: Dict[str, cp_model.IntVar] = {}
        for train in trains:
            d[train.train_id] = model.NewIntVar(
                0, self.horizon_minutes, f"d_{train.train_id}"
            )

        # Constraints: Platform capacity & Block Exclusivity + Minimum Headway
        for s in B:
            cap = capacities[s]
            for t in T:
                # To account for minimum headway, we ensure the rolling sum of x
                # over [t, t + headway) doesn't exceed the component capacity.
                # Only apply headway buffer to track blocks, stations can host train continuously.
                window_end = (
                    min(t + self.min_headway_min, self.horizon_minutes)
                    if s in B_track
                    else t + 1
                )

                sum_vars = []
                for train in trains:
                    for t_prime in range(t, window_end):
                        sum_vars.append(x[(train.train_id, s, t_prime)])

                model.Add(sum(sum_vars) <= cap)

        # Travel time consistency (stub formulation so the solver is mathematically valid)
        for train in trains:
            i = train.train_id
            # A train must occupy exactly one block/station at any given time t
            for t in T:
                model.AddExactlyOne([x[(i, s, t)] for s in B])

        # Objective: minimize SUM(w_i * d_i)
        objective_terms = []
        for train in trains:
            w_i = get_priority_weight(train.priority)
            objective_terms.append(w_i * d[train.train_id])

        model.Minimize(sum(objective_terms))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.timeout_sec
        status = solver.Solve(model)

        schedules = {}
        if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
            for train in trains:
                tid = train.train_id
                path = []
                for s in B:
                    for t in T:
                        if solver.Value(x[(tid, s, t)]) == 1:
                            path.append((s, t + start_time_min))
                path.sort(key=lambda item: item[1])
                schedules[tid] = path

        return schedules
