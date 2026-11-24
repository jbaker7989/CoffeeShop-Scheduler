# BUG-063 — make the test-first CI guard see the `coffee-scheduler/` layout (Plan)

## The defect, precisely

`scripts/ci/check_test_first.py` enforces that the first branch commit adding a `test/` file
precedes the first commit adding production code. It classifies paths with:

```python
PRODUCTION_PREFIXES = ("auth/", "compliance/", "data_access/", "api/", "agents/", "db/", "otel/", "n8n/", "contracts/")
def _is_production(path): ... return path.startswith(PRODUCTION_PREFIXES)
```

against the output of `git log --name-only`. `git log` emits paths **relative to the git
repository root**, and this project lives in a `coffee-scheduler/` subdirectory of that repo, so
the reported paths are `coffee-scheduler/contracts/forecast.py`, `coffee-scheduler/db/…`, etc.
None of those start with `contracts/` or `db/`, so `_is_production` is **always `False`**, the
guard prints "OK — no production files added; nothing to order" for every PR, and test-first is
never enforced. (`ROOT = parents[2]` is `coffee-scheduler/`, but running `git log` there still
yields repo-root-relative paths, so changing `cwd` does not help.)

## The fix: normalise paths by the repo subdirectory

- Discover the subtree prefix once with `git -C ROOT rev-parse --show-prefix` → `coffee-scheduler/`
  (or `""` when `coffee-scheduler/` *is* the repo root, e.g. a sub-tree checkout). Fail-closed: if
  that call fails, surface it the same way the existing base-ref resolution does.
- Strip that prefix from every `git log` path before classifying. A path outside the subtree
  (a repo-root file) does not start with the prefix and is simply not our test/production file, so
  it is ignored — the guard is scoped to this project's tree exactly as before, just correctly.
- `_is_test`, `_is_production`, the excluded prefixes and the ordering logic are unchanged; they
  now operate on normalised paths.

## Test dev (the self-test the AC requires)

Add `test/test_bug063_test_first_guard.py`, driving the module's pure functions so it needs no
real git repo (fast, deterministic, CI-safe):

- **normalisation:** `coffee-scheduler/contracts/x.py` → `contracts/x.py`; a `""` prefix is a
  no-op; a repo-root path stays outside the subtree.
- **the regression:** `_is_production` returns `True` for a normalised `contracts/…`/`db/…`/`api/…`
  path (it returned `False` before the fix), and `_is_test` for `test/…`.
- **ordering end-to-end:** feed synthetic commit lists (sha, `coffee-scheduler/`-prefixed paths)
  through the classify step and assert: production-before-test → non-zero exit; test-before- or
  with-production → zero; production with no test on the branch → non-zero.
- **excluded prefixes keep their meaning** after normalisation: `test/`, `scripts/ci/`, `docs/`,
  `db/migrations/versions/` are neither test-ordering triggers nor production.

## Acceptance criteria

- **ac1:** paths are normalised (leading `coffee-scheduler/` stripped) so `contracts/`, `db/`,
  `api/`, `agents/`, etc. match the production/test classifiers.
- **ac2:** a self-test proves a commit adding production code before its test fails the guard and
  a correctly ordered pair passes.
- **ac3:** the excluded prefixes keep their current meaning after normalisation; a sub-tree
  checkout (empty prefix) behaves exactly as today.
