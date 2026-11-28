# CS-089 — One authoritative workweek

The authoritative workweek is the ISO week: Monday 00:00 through Sunday 24:00 in local
`America/New_York` time. Hour caps, guaranteed-hours shortfall, overtime, and payroll use this
boundary. `legality_from_compliance.week_start` is the Monday of the slot's ISO week. A configurable
employer week is out of scope; adding one later changes only `workweek`.

Overtime is calculated independently for each workweek: half-hours beyond 80 are summed across
weekly slacks. Previously a multi-week window compared all its hours to one 80-half-hour limit;
the corrected calculation leaves a Monday-through-Sunday window unchanged and fixes longer or
non-Monday-aligned windows.

## Acceptance criteria

**F11.ac65**: `workweek` and `workweek_days` use ISO Monday boundaries.

**F11.ac66**: Overtime is charged per ISO week rather than once per target window.

**F11.ac67**: A non-Monday-aligned seven-day window spans two workweeks, with bounds applied to
each and no full-week guarantee.

**F11.ac68**: Compliance receives the Monday starting the slot's workweek.

**F11.ac69**: A Monday-through-Sunday week retains the prior weekly overtime behaviour.

## Traceability

| Acceptance criterion | Test |
| --- | --- |
| F11.ac65 | `WorkweekTests.test_ac65_helper_uses_iso_monday_boundaries` |
| F11.ac66 | `WorkweekTests.test_ac66_overtime_is_per_week_not_window` |
| F11.ac67 | `WorkweekTests.test_ac67_non_monday_window_spans_two_iso_weeks` |
| F11.ac68 | `WorkweekTests.test_ac68_compliance_receives_slot_workweek_monday` |
| F11.ac69 | `WorkweekTests.test_ac69_monday_sunday_overtime_is_unchanged` |
