# CS-016c — Soft objectives & cost function

CS-016c adds an integer-only CP-SAT objective to the existing hard model. It is verified on small
synthetic feasible models and a rollback-gated one-day build; it does not claim a real full-week
optimisation while BUG-072 and BUG-073 remain open.

| Default weight | Value | Trade-off |
| --- | ---: | --- |
| `labour_cost` | 1 | Minimises cents paid when service penalties are equal. |
| `understaffing` | 100 | Strongly prefers covering planned labour while retaining a feasible model. |
| `overtime` | 10 | Discourages half-hours beyond 40 weekly hours. |
| `premium` | 1 | Prices legal compliance premiums without excluding the assignment. |
| `labour_pct_breach` | 10 | Discourages labour spend above the sales-derived target. |

Demand uses the p70 item forecast and throughput seconds per item, divided by the configured
utilisation target. Labour cost and premiums are converted once to cents; time is represented in
half-hour units. The labour-percent term uses a revenue-per-item prior derived from observed
`forecast_features` training rows for the named feature version. A missing prior omits that
store's labour-percent term and records the reason; it is never treated as zero sales.

## Acceptance criteria

**F11.ac16**: Required labour is a pure p70/throughput/utilisation calculation, and legal lower-cost assignments are preferred.

**F11.ac17**: Understaffing is represented by a non-negative per-store-hour slack and is penalised without becoming a hard constraint.

**F11.ac18**: Overtime beyond 40 weekly hours is a non-negative half-hour slack, priced using the home store's overtime budget.

**F11.ac19**: Allowed compliance premiums are retained on the model, converted to cents, and dispreferred when otherwise equal.

**F11.ac20**: Frozen, integer `ObjectiveWeights` make tuning explicit; changing a weight changes the selected synthetic schedule in its documented direction.

**F11.ac21**: Objective construction is deterministic and integer-only. The rollback-gated check builds it for one forward day and preserves recorded omissions.

## AC-to-test traceability

| AC | Tests |
| --- | --- |
| F11.ac16 | `test_ac16_required_labour_hours_is_pure`, `test_ac16_cheaper_legal_assignment_is_preferred` |
| F11.ac17 | `test_ac17_understaffing_is_soft_and_penalised` |
| F11.ac18 | `test_ac18_overtime_slack_is_penalised` |
| F11.ac19 | `test_ac19_premium_assignment_is_dispreferred` |
| F11.ac20 | `test_ac20_weights_change_the_chosen_solution` |
| F11.ac21 | `test_ac21_objective_remains_feasible_and_deterministic`, `test_ac21_real_one_day_objective_builds` |
