# CS-094 — Open-hour coverage

**F11.ac34**: A whole open day with no candidate slots raises `SolverModelError` naming the store,
day, hour, and `COV-MIN-CREW` rather than producing a satisfiable unstaffed model.

**F11.ac35**: A caller-supplied sparse slot grid raises for an uncovered open hour.

**F11.ac36**: A calendar day is open for coverage only when its weekday is in the window's
`open_weekdays`, matching `build_slot_grid`.

**F11.ac37**: A used open window with `close_hour <= open_hour` is rejected with a typed error
naming the store and weekday.

**F11.ac38**: A sub-hour slot that covers no integer hour does not satisfy an open-hour minimum.

**F11.ac39**: Slots without a required role remain solver infeasibility under the existing
coverage assumption group, rather than becoming a build-time error.

**F11.ac40**: A fully coverable 06:00–21:00 window with a four-hour minimum shift builds without
an open-hour coverage error.

| AC | Test |
| --- | --- |
| F11.ac34 | `test_ac34_whole_day_empty_grid_raises_for_minimum_crew` |
| F11.ac35 | `test_ac35_partial_day_gap_in_caller_slots_raises` |
| F11.ac36 | `test_ac36_open_weekday_mismatch_is_closed_for_coverage` |
| F11.ac37 | `test_ac37_degenerate_open_window_raises` |
| F11.ac38 | `test_ac38_sub_hour_slot_does_not_cover_an_integer_hour` |
| F11.ac39 | `test_ac39_role_shortfall_remains_coverage_group_infeasibility` |
| F11.ac40 | `test_ac40_fully_coverable_four_hour_window_builds` |
