# CS-016b: CP-SAT hard constraints

**F11.ac8**: Legality is pre-computed through CS-013 per candidate assignment and encoded as an exclusion; it is never recomputed during CP-SAT search. Premium outcomes remain legal metadata.

**F11.ac9**: Coverage enforces minimum crew, open/close key-holder, food-protection certification, peak skill, and break-relief requirements.

**F11.ac10**: Concurrent assignments never exceed a store's station count or maximum floor headcount.

**F11.ac11**: Shift candidates and assignments enforce lengths, half-hour increments, five consecutive-day maximum, ten-hour rest, no split shifts, and weekly hour bounds.

**F11.ac12**: Employees remain at their home location unless marked cross-location eligible.

**F11.ac13**: Infeasible models report CP-SAT assumption-core constraint-group names.

**F11.ac14**: The deterministic model has no objective; later stories own optimisation and execution.

| Acceptance criterion | Tests |
| --- | --- |
| F11.ac8 | `test_ac8_legality_exclusion` |
| F11.ac9 | `test_ac9_coverage_groups` |
| F11.ac10 | `test_ac10_station_capacity` |
| F11.ac11 | `test_ac11_shift_rules_fail_closed` |
| F11.ac12 | `test_ac12_home_location` |
| F11.ac13 | `test_ac13_diagnoses_assumption_core` |
| F11.ac14 | `test_ac14_model_has_no_objective` |

## BUG-071 — legality partition (2026-11-23)

Pre-computing legality is only valid for rules one candidate assignment can decide. The original
adapter fed every rule a synthetic context (`crew=[]` with `crew_complete=True`, `breaks=[]`), so
crew-scoped and break-dependent rules failed for every candidate and all 39,600 assignments were
excluded. A rule is now either decidable per assignment or enforced as a CP-SAT constraint.

8. **F11.ac15**: only per-assignment-decidable metrics are pre-computed and encoded; schedule-scoped
   (`hours_per_week`, `days_per_week`, `rest_hours_between_shifts`), crew-scoped
   (`certified_staff_on_site`), break-dependent (`break_minutes`) and call-out/posting rules are
   excluded from the pre-computation, no crew-scoped rule ever excludes a candidate, and the model
   retains legal candidates to choose from.

| Acceptance criterion | Tests |
| --- | --- |
| F11.ac15 | `LegalityPartitionTests.test_ac15_partition_excludes_schedule_crew_and_break_metrics` · `LegalityPartitionDatabaseTests.test_ac15_crew_rules_do_not_exclude_candidates` |
