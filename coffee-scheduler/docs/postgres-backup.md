# PostgreSQL backup and restore drill

This runbook uses the Compose `postgres` container.  Run Alembic migrations against
the direct port `5432`; application traffic uses PgBouncer on `6432`.

## Scheduled backup and retention

Create a local destination once:

```sh
mkdir -p backups/postgres
```

Run this daily from an n8n Schedule Trigger, cron, or the deployment scheduler.  It
creates a custom-format archive, which supports selective and parallel restore.

```sh
docker compose exec -T postgres pg_dump -U coffeeshop -Fc -d coffeeshop > "backups/postgres/coffeeshop-$(date +%F).dump"
```

Keep 30 daily archives, 12 month-end archives, and at least one restore-drill
archive.  After confirming the new archive is non-empty, remove only daily files
older than 30 days:

```sh
find backups/postgres -type f -name 'coffeeshop-*.dump' -mtime +30 -delete
```

Use off-host encrypted storage for the retained archives; a Docker volume is not a
backup.

## Restore drill

Run this at least quarterly against a scratch database, never `coffeeshop`.  Replace
the archive path with the file produced above.

```sh
docker compose exec -T postgres dropdb -U coffeeshop --if-exists coffeeshop_restore_drill
docker compose exec -T postgres createdb -U coffeeshop coffeeshop_restore_drill
docker compose exec -T postgres pg_restore -U coffeeshop -d coffeeshop_restore_drill --exit-on-error < backups/postgres/coffeeshop-YYYY-MM-DD.dump
docker compose exec -T postgres psql -U coffeeshop -d coffeeshop_restore_drill -v ON_ERROR_STOP=1 -c "SELECT count(*) AS tenant_count FROM tenants;"
```

The next commands put one `stores` row in each of two tenants in the scratch database
and prove that a transaction-local role and tenant setting expose only the selected
tenant.  They do not change the production database.

```sh
docker compose exec -T postgres psql -U coffeeshop -d coffeeshop_restore_drill -v ON_ERROR_STOP=1 -c "INSERT INTO tenants (id, slug, name, status) VALUES ('00000000-0000-0000-0000-000000000002', 'restore-check', 'Restore Check', 'active') ON CONFLICT (id) DO NOTHING; INSERT INTO stores (tenant_id, location_id, store_location) VALUES ('00000000-0000-0000-0000-000000000001', 91001, 'demo check'), ('00000000-0000-0000-0000-000000000002', 91002, 'other check') ON CONFLICT DO NOTHING;"
docker compose exec -T postgres psql -U coffeeshop -d coffeeshop_restore_drill -v ON_ERROR_STOP=1 -c "BEGIN; SET ROLE app_ro; SELECT set_config('app.tenant_id', '00000000-0000-0000-0000-000000000001', true); SELECT count(*) AS visible_store_count FROM stores WHERE location_id IN (91001, 91002); ROLLBACK;"
```

The final query must return `visible_store_count = 1`.  Record the archive date,
restore duration, tenant count, and RLS result in the operational log.
