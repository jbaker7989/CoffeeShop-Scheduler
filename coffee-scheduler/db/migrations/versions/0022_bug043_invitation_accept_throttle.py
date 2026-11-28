"""BUG-043: persist anonymous invitation-accept IP throttling behind auth_svc."""
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


UPGRADE_SQL = r'''
CREATE TABLE auth_invitation_accept_throttle (
    client_ip INET PRIMARY KEY,
    failures TIMESTAMPTZ[] NOT NULL DEFAULT ARRAY[]::timestamptz[],
    locked_until TIMESTAMPTZ NULL,
    last_attempt_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX auth_invitation_accept_throttle_last_attempt_idx
    ON auth_invitation_accept_throttle(last_attempt_at);
REVOKE ALL ON TABLE auth_invitation_accept_throttle FROM PUBLIC, app_rw, app_ro, auth_svc;

CREATE FUNCTION auth_reserve_invitation_accept(
    p_ip inet,
    p_now timestamptz,
    p_max_failures integer,
    p_window_seconds integer,
    p_lockout_seconds integer
)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    v_failures timestamptz[] := ARRAY[]::timestamptz[];
    v_locked_until timestamptz;
    v_failure_count integer;
    v_cutoff timestamptz;
BEGIN
    IF p_ip IS NULL OR p_now IS NULL OR p_max_failures IS NULL OR p_max_failures NOT BETWEEN 1 AND 100
       OR p_window_seconds IS NULL OR p_window_seconds NOT BETWEEN 1 AND 86400
       OR p_lockout_seconds IS NULL OR p_lockout_seconds NOT BETWEEN 1 AND 86400 THEN
        RETURN true;
    END IF;

    -- Reclaim inactive IP state before taking a per-IP lock to avoid lock-order
    -- cycles between concurrent cleanup and reservations for different IPs.
    DELETE FROM auth_invitation_accept_throttle
     WHERE locked_until <= p_now
        OR (locked_until IS NULL AND last_attempt_at <= p_now - make_interval(secs => p_window_seconds));
    PERFORM pg_advisory_xact_lock(hashtextextended('auth-invite-accept:' || p_ip::text, 0));
    SELECT failures, locked_until
      INTO v_failures, v_locked_until
      FROM auth_invitation_accept_throttle
     WHERE client_ip = p_ip
     FOR UPDATE;

    IF FOUND AND v_locked_until > p_now THEN
        RETURN true;
    ELSIF FOUND AND v_locked_until IS NOT NULL THEN
        -- The existing lock duration expires the whole failure bucket, as it does
        -- for LoginRateLimiter; exact-boundary requests begin a fresh window.
        DELETE FROM auth_invitation_accept_throttle WHERE client_ip = p_ip;
        v_failures := ARRAY[]::timestamptz[];
        v_locked_until := NULL;
    END IF;

    v_cutoff := p_now - make_interval(secs => p_window_seconds);
    SELECT coalesce(array_agg(failed_at ORDER BY failed_at), ARRAY[]::timestamptz[])
      INTO v_failures
      FROM unnest(v_failures) AS attempts(failed_at)
     WHERE failed_at > v_cutoff;
    v_failure_count := cardinality(v_failures);

    IF v_failure_count >= p_max_failures THEN
        v_locked_until := p_now + make_interval(secs => p_lockout_seconds);
        INSERT INTO auth_invitation_accept_throttle(client_ip, failures, locked_until, last_attempt_at)
        VALUES (p_ip, v_failures, v_locked_until, p_now)
        ON CONFLICT (client_ip) DO UPDATE
          SET failures = EXCLUDED.failures, locked_until = EXCLUDED.locked_until,
              last_attempt_at = EXCLUDED.last_attempt_at;
        RETURN true;
    END IF;

    -- Count this request before credential validation. The threshold-th request
    -- is allowed to finish; later concurrent requests observe the lock.
    v_failures := array_append(v_failures, p_now);
    IF cardinality(v_failures) >= p_max_failures THEN
        v_locked_until := p_now + make_interval(secs => p_lockout_seconds);
    ELSE
        v_locked_until := NULL;
    END IF;
    INSERT INTO auth_invitation_accept_throttle(client_ip, failures, locked_until, last_attempt_at)
    VALUES (p_ip, v_failures, v_locked_until, p_now)
    ON CONFLICT (client_ip) DO UPDATE
      SET failures = EXCLUDED.failures, locked_until = EXCLUDED.locked_until,
          last_attempt_at = EXCLUDED.last_attempt_at;
    RETURN false;
END
$$;

CREATE FUNCTION auth_complete_invitation_accept(p_ip inet, p_succeeded boolean, p_now timestamptz)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF p_ip IS NULL OR p_now IS NULL THEN
        RAISE EXCEPTION 'invalid invitation throttle completion';
    END IF;
    IF p_succeeded IS NOT TRUE THEN
        RETURN;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('auth-invite-accept:' || p_ip::text, 0));
    DELETE FROM auth_invitation_accept_throttle WHERE client_ip = p_ip;
END
$$;

REVOKE ALL ON FUNCTION auth_reserve_invitation_accept(inet,timestamptz,integer,integer,integer) FROM PUBLIC, app_rw, app_ro;
REVOKE ALL ON FUNCTION auth_complete_invitation_accept(inet,boolean,timestamptz) FROM PUBLIC, app_rw, app_ro;
GRANT EXECUTE ON FUNCTION auth_reserve_invitation_accept(inet,timestamptz,integer,integer,integer) TO auth_svc;
GRANT EXECUTE ON FUNCTION auth_complete_invitation_accept(inet,boolean,timestamptz) TO auth_svc;
'''


def upgrade() -> None:
    op.execute(UPGRADE_SQL)


def downgrade() -> None:
    op.execute('''
DROP FUNCTION IF EXISTS auth_complete_invitation_accept(inet,boolean,timestamptz);
DROP FUNCTION IF EXISTS auth_reserve_invitation_accept(inet,timestamptz,integer,integer,integer);
DROP TABLE IF EXISTS auth_invitation_accept_throttle;
''')
