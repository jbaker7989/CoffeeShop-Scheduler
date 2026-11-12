# CS-005 — Closing-hours decision & operating-hours config

**Story:** [CS-005](../../Backlog.md) · **Feature:** [F4](../../FEATURES.md) · **Priority:** P0
**Status:** ✅ Decided (2026-11-12) — adopted the suggested answer set (§6); encoded in `coffee_shop_labor_standards.xlsx`.
**Decision needed by:** before CS-016 (solver) starts. — met.
**Author:** Project owner (approved) · **Reviewers:** —

---

## 0. Decision (2026-11-12)

Adopted the **suggested answer set** in §6:

| Store | Open | Close | First staff in | Last staff out |
|---|---|---|---|---|
| Lower Manhattan (5) | 06:00 | **21:00** | 05:15 | 21:45 |
| Hell's Kitchen (8) | 06:00 | **21:00** | 05:15 | 21:45 |
| Astoria (3) | **07:00** | **20:30** | 06:15 | 21:15 |

Evening crew policy **Q4 = C (no dead hours)** — close lands at last-tx + ~30 min, so no evening
minimum-crew rule is added. 45-min pre-open / post-close windows retained.

**Encoded by** `data/apply_closing_hours.py` into `Store_Profile` (open/close; staff-in/out are
auto-derived formulas) and `Hours_Validation` (hours-to-close recomputed; Finding note refreshed).
OQ-7 handled as a data fix (Astoria 06:00→07:00). This unblocks CS-016 (solver time horizon).

---

## 1. Context

The coffee shop scheduler needs a settled, machine-readable operating-hours window per store. The current `Store_Profile` sheet in `coffee_shop_labor_standards.xlsx` encodes a single set of hours (06:00–22:30 for LM/HK, 06:00–22:30 for Astoria) that **does not match the observed sales data** — Astoria opens at 07:00 in practice, every store's last meaningful transaction is between 19:59 and 20:59, and the configured 22:30 close implies 1.5–2.5 hours of dead crew per store per day.

CS-005 settles the four open questions below and encodes the answers into `coffee_shop_labor_standards.xlsx`. Until CS-005 closes, the solver (CS-016) cannot run — its time horizon is undefined.

## 2. What the data says

Source: `Hours_Validation` sheet in `coffee_shop_labor_standards.xlsx` (auto-derived from `coffee_shop_sales.xlsx`, Jan–Jun 2023, 3 stores).

| Store | First transaction (typical) | Last transaction (typical) | Hours of sales | Hours 06:00–22:30 dead | Hours 22:30 close to last staff out |
|---|---|---|---|---|---|
| Lower Manhattan (5) | 06:00–06:00:31 | 20:16–20:28 | ~14.5 | ~2.0 | ~0.75 |
| Hell's Kitchen (8) | 06:30 | 20:58–20:59 | ~14.5 | ~1.5 | ~0.25 |
| Astoria (3) | **07:00** | 19:59:43–19:59:54 | ~13.0 | ~2.5 | ~0.75 |

Two clear facts from the data:

1. **Astoria opens at 07:00**, not 06:00. The `Store_Profile` sheet currently says 06:00 for Astoria — that's a known data error (drift), independent of the decision.
2. **Every store's last meaningful transaction is between 20:00 and 21:00.** After 21:00 the data has fewer than 1 transaction per hour per store.

The 6-month sample is 2023 only; no annual seasonality (no evening hours sales history). The 2023 numbers are the only evidence base.

## 3. What the policy says today

Per `Coffee Shop Scheduler — Project Status.md` § 1:

> Hours: 6:00 AM–10:30 PM, with 45 minutes before open and after close.

This is the status quo. **22:30 close** for all three stores; **45-minute pre-open and post-close** staffing windows. The plan assumed this without checking it against the sales data.

## 4. The four open questions

These are the questions the business must answer. Each has options with rough cost / risk implications. A placeholder default is suggested for each so the work can proceed even if no one is available to decide — but the placeholder should be flipped as soon as a real answer is available.

### Q1 — Close hour for Lower Manhattan (store_id 5) and Hell's Kitchen (store_id 8)?

| Option | Close | Rationale | Crew savings vs status quo | Customer risk |
|---|---|---|---|---|
| **A** | 22:30 | Status quo. Matches the Project Status doc. | None (baseline). | None. |
| **B** | 21:00 | Matches last-tx + ~30 min buffer; cuts 1.5 h of dead crew per store per day. | ~6 staff-hours/store/week (rough). | Any customer arriving 21:00–22:30 turned away. The 2023 data has <1 tx/hr in this window, but it's not zero. |
| **C** | 21:30 | Compromise — keeps 1 hour of buffer past last tx. | ~3 staff-hours/store/week. | Lower than B; rare 21:30–22:30 customer turned away. |

**Suggested default (placeholder):** **B — 21:00**. Last-tx + 30 min is the right shape, and the dead-crew cost is real.

### Q2 — Close hour for Astoria (store_id 3)?

| Option | Close | Rationale | Crew savings vs status quo | Customer risk |
|---|---|---|---|---|
| **A** | 22:30 | Match LM/HK. Operational simplicity. | None. | Highest — 2.5 h of dead crew per day. |
| **B** | 20:30 | Matches last-tx + 30 min buffer. Cuts 2 h of dead crew per day. | ~8 staff-hours/store/week. | Astoria closes earliest of the three. Inconsistent with LM/HK if Q1 = A or C. |
| **C** | 21:00 | Match Q1 = B. Operational consistency across all three stores. | ~6 staff-hours/store/week. | 20:30–21:00 window served in LM/HK but not Astoria. |

**Suggested default (placeholder):** **B — 20:30**. Astoria's last tx is 19:59 — it has the most to gain from a tighter close. If the answer to Q1 is B (21:00), consider C (21:00) for consistency; flag as Outstanding question.

### Q3 — Open hour for Astoria (store_id 3)?

| Option | Open | Rationale | Crew cost vs status quo | Customer upside |
|---|---|---|---|---|
| **A** | 07:00 | Matches observed sales. The 6-month data shows zero transactions before 07:00:00 at Astoria. | Saves 1 h of crew per day vs current config (which says 06:00). | None — there are no 06:00–07:00 customers in the data. |
| **B** | 06:00 | Match LM/HK. Operational symmetry. | Adds 1 h of crew per day. | Captures any 06:00–07:00 foot traffic the 2023 sample misses (new-build area, organic growth). |

**Suggested default (placeholder):** **A — 07:00**. The data is unambiguous; the `Store_Profile` row for Astoria is the drift that needs fixing regardless.

### Q4 — Evening crew policy during dead hours?

Applies when close time is later than last-tx + 30 min. If Q1/Q2 already aligns with last-tx + 30 min, this question is moot — pick option **C**.

| Option | Policy | Cost | Risk |
|---|---|---|---|
| **A** | 1 key-holder on floor during dead hours | ~1 staff-hour/store/day | If a customer arrives, they're served by a key-holder (no barista trained to make drinks during the dead window — should still be able to). |
| **B** | 1 key-holder + 1 barista | ~2 staff-hours/store/day | Full service maintained during dead hours. |
| **C** | No dead hours — close at Q1/Q2 boundary | None | Closes any after-hours possibility. |

**Suggested default (placeholder):** **C** if Q1 = B and Q2 = B (no dead hours at all). **A** if Q1 or Q2 keeps a dead window.

## 5. Outstanding questions

These are the things the business must confirm before CS-005 can be marked Done. They are the same as Q1–Q4 above but framed as yes/no asks with explicit data behind each one.

### OQ-1 — Confirm close hour for LM and HK

> Do Lower Manhattan and Hell's Kitchen close at **21:00** (matching last-tx + ~30 min), or keep **22:30** (status quo)?

- **Data behind the ask:** Last transaction in LM is 20:16–20:28; in HK is 20:58–20:59. Dead-crew cost at 22:30 close is ~1.5–2 h of crew per store per day with <1 transaction per hour in the dead window.
- **Needed from:** Operating decision-maker (store ops / GM).
- **If no answer by EOD tomorrow:** Use placeholder **B (21:00)** and flag in `Backlog.md`.
- **Side effect if answer = B:** Cuts `last_staff_out` from 23:15 to 21:45. F11 (solver) needs the new horizon. F5 (calibration) needs re-running against the new evening demand.

### OQ-2 — Confirm close hour for Astoria

> Does Astoria close at **20:30** (matching last-tx + ~30 min), **21:00** (matching LM/HK if Q1 = B), or **22:30** (status quo)?

- **Data behind the ask:** Astoria's last transaction is 19:59:43–19:59:54 — earlier than LM or HK. Dead-crew cost at 22:30 close is ~2.5 h per day.
- **Needed from:** Operating decision-maker.
- **If no answer by EOD tomorrow:** Use placeholder **B (20:30)** unless OQ-1 answers B (then prefer C for consistency).
- **Side effect if answer = A (22:30):** Astoria keeps ~2.5 h of dead crew per day. The Project Status doc explicitly flags Astoria as needing attention here.

### OQ-3 — Confirm Astoria's open hour

> Does Astoria open at **07:00** (matches observed sales), or **06:00** (match LM/HK)?

- **Data behind the ask:** Sales data shows zero transactions at Astoria before 07:00. The current `Store_Profile` row says 06:00 — that's a known data error independent of the decision.
- **Needed from:** Operating decision-maker (or just acknowledge the data fix).
- **If no answer by EOD tomorrow:** Use **A (07:00)** — the data is unambiguous.
- **Side effect if answer = B:** The current 06:00 config is the *as-is* truth; we keep it. But the data shows no demand, so this answer would mean "we're paying for crew we don't need" — needs explicit rationale.

### OQ-4 — Confirm evening crew policy

> If close time > last-tx + 30 min, what crew stays on during the dead window?
> - 1 key-holder only (cheapest)
> - 1 key-holder + 1 barista (full service)
> - No dead hours (close at Q1/Q2 boundary — moot if Q1/Q2 already align)

- **Data behind the ask:** Affects `Coverage_Rules.COV-EVENING-MIN` (new row) and `Cost_Targets.understaffing_penalty_weight` for evening hours.
- **Needed from:** Operating decision-maker.
- **If no answer by EOD tomorrow:** Use **C** if Q1 = B and Q2 = B; **A** otherwise.
- **Side effect:** If A or B, F26 (Coverage_Rules ↔ Labor_Rules reconciliation) gains a new row to check.

### OQ-5 — Does LM or HK have evening foot traffic the 2023 sample misses?

> The 2023 sales data has <1 transaction per hour between 21:00 and 22:30. If a new tenant, residential building or seasonal event has changed the evening picture since 2023, Q1's answer should not be B (21:00) without validation.

- **Needed from:** Operating decision-maker with current-staff knowledge.
- **If no answer:** Risk of turning away 21:00–22:30 customers. Acceptable for the demo; flag for production.
- **How to validate cheaply:** Manager survey of two weeks of barista closing-time anecdotes. No new data pipeline needed.

### OQ-6 — Confirm the placeholder answers can ship before real answers arrive

> If OQ-1 / OQ-2 / OQ-3 / OQ-4 are not answered in time, can the placeholder defaults (B / B / A / C-or-A) ship and be flipped later?

- **Why it matters:** CS-016 (solver) is blocked on CS-005. If placeholders unblock the solver, the engine starts running against the placeholder config; any later flip is a config edit (Store_Profile + Hours_Validation) and a F48 cron re-run.
- **Needed from:** Project owner / engineering lead.
- **Default if no answer:** Yes — ship placeholders, flip on real answers. Document the flip in this decision doc's changelog (Section 7).

### OQ-7 — Astoria 07:00 open is a fix, not a decision

> The current `Store_Profile` row for Astoria says 06:00 but the data says 07:00. This is a data error, not a business call. Confirm we fix it as part of CS-005 without treating it as a Q3 decision.

- **Why it matters:** If Q3 is treated as "do we change Astoria's open hour?" the answer feels like a big call. If it's framed as "the data says 07:00, do we follow the data?" it's a fix.
- **Needed from:** Operating decision-maker — just an acknowledgement.
- **Default if no answer:** Treat as a data fix; encode 07:00; flag in PR description.

## 6. Suggested answer (for the impatient decision-maker)

| Q | Suggested answer | Rationale |
|---|---|---|
| Q1 | **B — 21:00 close for LM & HK** | Last-tx + 30 min. Saves ~1.5 h dead crew per store per day. <1 tx/hr in the 21:00–22:30 window. |
| Q2 | **B — 20:30 close for Astoria** | Last-tx + 30 min. Saves ~2 h dead crew per day. |
| Q3 | **A — 07:00 open for Astoria** | Matches data. The 06:00 row is a data error; fix it. |
| Q4 | **C — no dead hours** | Q1 = B and Q2 = B already align with last-tx + 30 min. No crew policy needed. |

If the answers diverge from this set, that's fine — encode whatever is decided. The suggestion is the smallest-cost option consistent with the data.

## 7. Changelog

- **2026-11-11** — Document created (CS-005 plan execution). Outstanding questions OQ-1 through OQ-7 listed. No answers yet. Status: Open.
- **2026-11-12** — Decided: adopted the suggested answer set (Q1=B 21:00 LM/HK, Q2=B 20:30 Astoria, Q3=A 07:00 Astoria, Q4=C no dead hours). Encoded into `coffee_shop_labor_standards.xlsx` via `data/apply_closing_hours.py`. OQ-5 (post-2023 evening foot-traffic) accepted as a demo-acceptable risk, flag for production. Status: Decided.
