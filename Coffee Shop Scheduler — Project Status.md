# Coffee Shop Scheduler — Project Status

Nov 10, 2026 · @Jason baker

Status of the Predictive Smart Shift Scheduler: what is decided, what is built, and what is still open.

## 1. Settled decisions

### Product and scope

- **Build target:** #4, the Predictive Smart Shift Scheduler, as the first build of a multi-agent hub, not a standalone app.
- **Later phases:** Prep/Waste (#3) and Inventory (#2) in Phase 2; the forecast-aware Loyalty agent (#5) in Phase 3. Schemas and Agent Cards are designed now and populated later.
- **Core principle:** the LLM never produces schedules or forecasts. A statistical/ML model forecasts, OR-Tools CP-SAT builds the schedule, and the LLM handles conversations, chooses the next tool, explains decisions and negotiates with staff.

### Architecture

- **n8n:** triggers and plumbing (cron jobs, POS sync, SMS webhooks, notifications, payroll export).
- **LangGraph:** stateful agent graphs, including the call-out workflow with long waits, loops and manager approval.
- **LangChain:** tool wrappers and prompt templates.
- **A2A:** used only across real boundaries. Demand Forecast, Labor Compliance, Staff Comms and Payroll/HCM are A2A services. One agent is built in a second framework to prove interoperability.
- **MCP:** connects agents to tools and data; A2A connects agents to other agents.

### Data and jurisdiction

- **Jurisdiction:** NY State plus NYC, matching the NYC sales data. Every rule is effective-dated; the strictest applicable rule wins.
- **Wage order:** Hospitality (Part 146), recorded as an assumption.
- **Fair Workweek:** a per-employer flag. Maven (3 locations) is not covered; the flag enables a covered-employer demo.
- **Key mapping:** location\_id = store\_id.
- **Store setups:**
  - Lower Manhattan (5) and Hell's Kitchen (8): 6 staff on floor, 4 stations.
  - Astoria (3): 4 staff on floor, 3 stations.
- **Hours:** per the CS-005 decision (2026-11-12): Lower Manhattan & Hell's Kitchen 06:00–21:00, Astoria 07:00–20:30, with 45 minutes before open and after close. (Was 6:00 AM–10:30 PM; shortened to last-tx + ~30 min. See `coffee-scheduler/docs/decisions/CS-005-closing-hours.md`.)
- **Minors' ages** are always calculated from date of birth on the shift date, never stored as a flag.

### File conventions

- A root AGENTS.md plus one per agent service.
- One SKILL.md per reusable procedure, not per agent.
- An Agent Card, prompt and tool schemas for each runtime agent.

## 2. Built so far

| File | Status | Contents |
| --- | --- | --- |
| coffee\_shop\_staff.xlsx | Done (identity only) | 40 staff: ID, name, sex, date of birth. 60% female, 8 minors (aged 15–17), 2 over 55 |
| coffee\_shop\_labor\_rules.xlsx | Done (needs verification) | 31 rules (20 verified, 10 to verify, 1 assumption), plus employer config, holidays and templates |
| coffee\_shop\_labor\_standards.xlsx | Done (assumptions) | Store profiles, stations, hours validation, 357-row hourly demand, throughput, coverage, fixed tasks, shift rules, cost targets, demand adjustments |
| build\_weather\_forecast.py | Fixed; not confirmed to have run | Pulls Open-Meteo weather per store and joins it to sales by hour and by day |
| weather\_forecast.xlsx | Not yet created | Produced when the script runs on the Mac |

## 3. Open items

### Blocking (needed before the scheduler can run)

- **Weather data:** run the fixed script, confirm weather\_forecast.xlsx was created, then check the data.
- **Roster scheduling fields:** roles, skill levels, home location, cross-location eligibility, key-holder status, cash authorisation, Food Protection Certificate, hourly rate, school enrolment, availability personas, and min/max hours. Without these the solver has nothing to schedule.
- **School calendar:** load the NYC public schools 2026–27 calendar. Every minor rule depends on it.
- **Closing-hours decision:** ✅ Resolved (CS-005, 2026-11-12). Shortened hours to last-tx + ~30 min: LM/HK close 21:00, Astoria 07:00–20:30; no dead-hour evening crew. Encoded in `coffee_shop_labor_standards.xlsx`.

### Compliance verification (before relying on the rules table)

- Meal-break rules, especially whether a 5:30 AM opener triggers the 45-minute midway break.
- Spread-of-hours applicability under Part 146.
- Fair Workweek premium tiers and the clopening premium (NYC DCWP).
- NYC Earned Safe and Sick Time: the agent may not require a sick employee to find cover.
- NYC Health Code: Food Protection Certificate holder required on site.
- Every statute section number cited from memory rather than a source opened in session.
- Occupation restrictions for 15-year-olds, mapped to roster roles.

### Assumptions to calibrate

- Throughput seconds per item for all 9 categories, plus the per-transaction register overhead (overstated because Maven has no basket ID).
- Weather and event multipliers in Demand\_Adjustments, to be set from the weather join.
- Target utilisation (85%), forecast quantile (P70), labour % targets, and the overtime budget.

### Design work not started

- **Agents:** root AGENTS.md, per-agent AGENTS.md, and the labor-rule-evaluation and other SKILL.md files.
- **Agent Cards:** Scheduler, Demand Forecast, Labor Compliance, Staff Comms, Payroll/HCM, plus placeholder cards for Inventory, Prep and Loyalty.
- **Demand Forecast output contract:** by product category per hour, with prediction intervals.
- **LangGraph state and node definitions:** schedule generation, and call-outs and swaps.
- **n8n workflows:** weekly forecast, nightly sync, inbound SMS, payroll export.
- **Staff Comms data:** contact preferences, SMS consent, message templates.
- **Forecast model:** choice and validation approach (6 months of data means no annual seasonality).
- **Square Sandbox replay** to simulate a live POS feed.
- **Observability:** one trace ID across A2A calls, authentication, timeouts.

### Known limitations to state openly

- Only 6 months of sales history, all from 2023.
- No basket ID in the sales data.
- No sales history for the evening hours.
- The free Open-Meteo API is non-commercial only.
- Staff, labour standards and adjustments are synthetic or assumed.

## 4. Suggested order

1. Run the weather script; then check the output and work out the adjustment multipliers.
2. Add the scheduling fields to the roster.
3. Load the school calendar and decide the closing hours.
4. Write the root AGENTS.md, the labor-rule-evaluation skill and the Agent Cards.
5. Build the forecast, then the solver, then the call-out graph.
6. Verify the compliance rules before any demo that claims legal accuracy.
