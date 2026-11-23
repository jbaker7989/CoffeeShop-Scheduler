# CS-092 build scaling

**F11.ac27**: The slot index matches a naive rescan for every slot's day, ISO week and store-hour.

**F11.ac28**: The interval encoding admits exactly the same feasible set as the pairwise encoding.

**F11.ac29**: A minimum rest is enforced on elapsed time across a daylight-saving transition, and
withdrawing the `shift_rules` assumption relaxes it.

**F11.ac30**: Two slots with identical bounds cannot both be held, and a single-slot grid builds.

**F11.ac31**: A zero minimum rest is refused.

**F11.ac32**: Equal inputs produce identical CP-SAT protos.

**F11.ac33**: The optimum is unchanged and the constraint count falls at least fivefold.

| AC | Test |
| --- | --- |
| F11.ac27 | `test_ac27_index_matches_naive_rescan` |
| F11.ac28 | `test_ac28_feasible_set_is_unchanged` |
| F11.ac29 | `test_ac29_rest_assumption_reification_and_dst_elapsed_time` |
| F11.ac30 | `test_ac30_identical_bounds_and_single_slot` |
| F11.ac31 | `test_ac31_zero_rest_is_refused_and_empty_bucket_is_empty` |
| F11.ac32 | `test_ac32_builds_are_proto_deterministic` |
| F11.ac33 | `test_ac28_optimum_and_constraint_reduction` |

## Why the feasible set, and not a chosen schedule

The objective has ties: several assignment sets cost exactly 26900 on the two-day fixture, so
re-encoding the rest rule changes which tied optimum CP-SAT reaches first. Asserting a chosen
schedule would pin an arbitrary tie-break rather than the model's meaning, so F11.ac28 enumerates
every feasible solution of a deliberately tiny fixture instead (2401 solutions, digest
`ebb4fb94e397f9bac5b6f86c702876fc563c7d1024b3da8a5c912f31e7eaabf0`, measured on the pairwise
encoding at `ba2a162` and keyed on slot identity rather than slot index).

## What this story does not claim

Solve time. Neither encoding proves optimality on the three-day, four-employee fixture inside 150s:
both reach 38300 in under a second and then stall. That is a pre-existing property of the objective,
measured on both encodings, and it is tracked as CS-093. F11.ac33 therefore caps the solve and
asserts the objective value rather than an `OPTIMAL` status.
