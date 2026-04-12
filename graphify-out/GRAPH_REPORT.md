# Graph Report - C:/Users/ritur/smartrail  (2026-04-12)

## Corpus Check
- Corpus is ~13,506 words - fits in a single context window. You may not need a graph.

## Summary
- 9 nodes · 8 edges · 3 communities detected
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 1 edges (avg confidence: 0.9)
- Token cost: 12,000 input · 800 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Core Architecture|Core Architecture]]
- [[_COMMUNITY_AI & Optimization|AI & Optimization]]
- [[_COMMUNITY_UI & Explainability|UI & Explainability]]

## God Nodes (most connected - your core abstractions)
1. `SmartRail System` - 5 edges
2. `Optimization Engine` - 3 edges
3. `Controller Dashboard UI` - 2 edges
4. `MILP Solver (OR-Tools)` - 1 edges
5. `Reinforcement Learning Agent` - 1 edges
6. `Discrete Event Simulation Engine` - 1 edges
7. `Train State Tracker` - 1 edges
8. `Conflict Detection Engine` - 1 edges
9. `Explainability Module` - 1 edges

## Surprising Connections (you probably didn't know these)
- `SmartRail System` --implements--> `Optimization Engine`  [EXTRACTED]
  SmartRail_Roadmap.pdf → SmartRail_Roadmap.pdf  _Bridges community 0 → community 1_
- `SmartRail System` --implements--> `Controller Dashboard UI`  [EXTRACTED]
  SmartRail_Roadmap.pdf → SmartRail_Roadmap.pdf  _Bridges community 0 → community 2_

## Communities

### Community 0 - "Core Architecture"
Cohesion: 0.5
Nodes (4): Conflict Detection Engine, Discrete Event Simulation Engine, SmartRail System, Train State Tracker

### Community 1 - "AI & Optimization"
Cohesion: 0.67
Nodes (3): MILP Solver (OR-Tools), Optimization Engine, Reinforcement Learning Agent

### Community 2 - "UI & Explainability"
Cohesion: 1.0
Nodes (2): Controller Dashboard UI, Explainability Module

## Knowledge Gaps
- **6 isolated node(s):** `MILP Solver (OR-Tools)`, `Reinforcement Learning Agent`, `Discrete Event Simulation Engine`, `Train State Tracker`, `Conflict Detection Engine` (+1 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `UI & Explainability`** (2 nodes): `Controller Dashboard UI`, `Explainability Module`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `SmartRail System` connect `Core Architecture` to `AI & Optimization`, `UI & Explainability`?**
  _High betweenness centrality (0.857) - this node is a cross-community bridge._
- **Why does `Optimization Engine` connect `AI & Optimization` to `Core Architecture`?**
  _High betweenness centrality (0.464) - this node is a cross-community bridge._
- **Why does `Controller Dashboard UI` connect `UI & Explainability` to `Core Architecture`?**
  _High betweenness centrality (0.250) - this node is a cross-community bridge._
- **What connects `MILP Solver (OR-Tools)`, `Reinforcement Learning Agent`, `Discrete Event Simulation Engine` to the rest of the system?**
  _6 weakly-connected nodes found - possible documentation gaps or missing edges._