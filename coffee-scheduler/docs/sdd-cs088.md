# CS-088 — Soft guaranteed hours

Guaranteed weekly hours are a fairness objective, not a hard shift rule. For every fully covered
ISO workweek and employee with a positive guarantee, the model records a non-negative half-hour
shortfall. It prices each short half-hour in integer cents at `_cents(hourly_rate / 2)`.

The default shortfall weight is 2. Because filling a half-hour costs the same base cents, this
weakly prefers honouring the guarantee while coverage (100) and overtime (10) remain stronger
priorities. A guarantee can never turn an otherwise feasible legal schedule infeasible. Weekly
maximums and invalid `min_hours_week > max_hours_week` configurations remain hard errors.

## Acceptance criteria

**F11.ac60**: A legal schedule below a guaranteed minimum remains feasible and exposes a positive
shortfall slack.

**F11.ac61**: Raising the shortfall weight increases booked hours for an otherwise under-booked
guaranteed employee.

**F11.ac62**: Zero guarantees and partial ISO weeks create no shortfall slack; partial positive
guarantees remain recorded in `skipped_minimums`.

**F11.ac63**: A contradictory configured minimum still fails closed, and the weekly maximum remains
a hard `shift_rules` bound.

**F11.ac64**: A fully met guarantee has zero shortfall.

## Traceability

| Acceptance criterion | Test |
| --- | --- |
| F11.ac60 | `GuaranteedHoursTests.test_ac60_minor_shortfall_is_feasible_with_penalty` |
| F11.ac61 | `GuaranteedHoursTests.test_ac61_higher_weight_books_more_guaranteed_hours` |
| F11.ac62 | `GuaranteedHoursTests.test_ac62_zero_and_partial_minimums_create_no_slack` |
| F11.ac63 | `GuaranteedHoursTests.test_ac63_invalid_minimum_and_hard_maximum_remain_hard` |
| F11.ac64 | `GuaranteedHoursTests.test_ac64_fully_met_guarantee_has_zero_shortfall` |
