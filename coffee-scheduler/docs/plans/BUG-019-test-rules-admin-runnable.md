# BUG-019 — make `test_rules_admin.py` (and its siblings) runnable as scripts (Plan)

## The defect, precisely

`test/test_rules_admin.py` defines 18 test methods (the BUG-014..018 rules-admin regressions) but
has **no `if __name__ == "__main__":` entrypoint** and **no `sys.path` insert**. Its own docstring
tells a developer to run it with `python3 coffee-scheduler/test/test_rules_admin.py`, yet that
invocation:

1. fails at import — `from compliance import rules_admin` raises `ModuleNotFoundError: No module
   named 'compliance'`, because a script run puts only the script's own `test/` directory on
   `sys.path`, not the package root `coffee-scheduler/` (every other first-party test file inserts
   the root explicitly); and
2. even with the import fixed, defines the classes and exits **0 with zero tests run**, because
   nothing calls `unittest.main()`.

So the F34 regressions only execute under `python3 -m unittest` discovery and silently guard
nothing in the repo's documented script workflow. A scan shows the identical missing-`__main__`
defect in three siblings — `test_bug051_pooled_adapter.py`, `test_bug052_single_validation.py`,
`test_bug053_logout_revocation.py` (these already insert the root, they only lack the entrypoint).
`test_taxonomy.py` is already runnable via its own `main()`.

## The fix

- **`test_rules_admin.py`:** insert the package root on `sys.path` at import time (matching the
  sibling convention) and append `if __name__ == "__main__":\n    unittest.main()`.
- **`test_bug051/052/053`:** append the same `unittest.main()` entrypoint; their path inserts are
  already present, so no import change is needed.
- Production code is untouched; this is a test-harness fix only.

## Test dev (the convention guard)

Add `test/test_bug019_test_harness_runnable.py`:

- A meta-guard that scans every `test/test_*.py` and asserts each contains an
  `if __name__ == "__main__":` entrypoint, so this "defines tests but never runs them" defect
  cannot recur anywhere in the suite. (It checks for a runnable entrypoint, not literally
  `unittest.main()`, because `test_taxonomy.py` legitimately uses its own `main()`.)
- A concrete regression for the named file: run `python3 test/test_rules_admin.py` in a
  subprocess and assert it reports **more than zero** tests and exits non-zero only on failure
  (here it should report its non-DB tests passing and its DB tests skipped).

## Acceptance criteria

- **ac1:** `python3 test/test_rules_admin.py` imports cleanly and runs its test methods (non-DB
  pass, DB-gated skip), instead of `ModuleNotFoundError` / zero tests.
- **ac2:** every `test/test_*.py` has a `__main__` entrypoint; the guard fails if one is added
  without it.
- **ac3:** the full suite under `python3 -m unittest discover -s test` is unchanged and green
  (discovery behaviour is not altered; only script-invocation is fixed).
