# CS-106 gateway maintenance

The BUG-031 shutdown handler closes a module-level pool. The next request calls `open()` on that same object. Psycopg raises `OperationalError` because a closed pool cannot be reused. One gateway can also close another gateway’s pool. The old tests substitute a mock module and never borrow a connection.

1. Exercise authenticated store reads across two lifespans of one app and across overlapping instances. Use the seeded PgBouncer integration so the request reaches the real role-scoped transaction.
2. Bind the default store and tenant-routing accessors to one database adapter per app. Create its pool on first use, serialize initialization, and discard the pool at shutdown so the next lifespan creates a fresh one.
3. Delete the unused unfiltered slug query. Keep the active/trial query and the tenant-scoped store transaction. Consolidate the identical access/refresh cookie counters without changing either transport.
4. Limit gateway database reachability checks to tests that query stores. Verify health, login and authorization with the gateway database offline. Run the existing duplicate-cookie and tenant-routing regressions.

## Related CI finding (CS-107)

Both CI checks used paths such as `coffee-scheduler/test/example.py` relative to a working directory already inside `coffee-scheduler/`. Traceability skipped the prefixed path and the checklist looked for a nonexistent file. Use a project-relative Git diff for both commands. Exercise both commands from the repository root against a nested project with a filename containing spaces. The fixture covers working-tree changes, committed PR changes, files outside the project, and correction of a previously failing change.
