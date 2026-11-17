"""CS-061b authentication hardening storage and least-privilege functions."""
from alembic import op
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

SQL = r'''
ALTER TABLE tenant_users ADD COLUMN sessions_revoked_at TIMESTAMPTZ NULL;
ALTER TABLE tenant_users ADD COLUMN mfa_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE tenant_users ADD COLUMN mfa_secret_ciphertext BYTEA NULL;
ALTER TABLE tenant_users ADD COLUMN mfa_last_timestep BIGINT NULL;
ALTER TABLE tenant_users ADD CONSTRAINT tenant_users_mfa_role CHECK (NOT mfa_enabled OR role IN ('owner', 'manager'));
CREATE TABLE auth_revoked_tokens (jti UUID PRIMARY KEY, tenant_id UUID NOT NULL, user_id UUID NOT NULL, revoked_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL, FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_users (tenant_id, id) ON DELETE CASCADE);
CREATE TABLE auth_password_reset_tokens (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL, user_id UUID NOT NULL, token_digest TEXT NOT NULL UNIQUE, expires_at TIMESTAMPTZ NOT NULL, consumed_at TIMESTAMPTZ, FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_users (tenant_id, id) ON DELETE CASCADE);
CREATE TABLE auth_mfa_recovery_codes (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id UUID NOT NULL, user_id UUID NOT NULL, code_digest TEXT NOT NULL UNIQUE, consumed_at TIMESTAMPTZ, FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_users (tenant_id, id) ON DELETE CASCADE);
CREATE TABLE auth_login_attempts (id BIGSERIAL PRIMARY KEY, tenant_id UUID NOT NULL, email CITEXT NOT NULL, ip INET NOT NULL, succeeded BOOLEAN NOT NULL, attempted_at TIMESTAMPTZ NOT NULL DEFAULT now());
ALTER TABLE auth_revoked_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth_password_reset_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth_mfa_recovery_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth_login_attempts ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON auth_revoked_tokens USING (tenant_id = app_current_tenant());
CREATE POLICY tenant_isolation ON auth_password_reset_tokens USING (tenant_id = app_current_tenant());
CREATE POLICY tenant_isolation ON auth_mfa_recovery_codes USING (tenant_id = app_current_tenant());
CREATE POLICY tenant_isolation ON auth_login_attempts USING (tenant_id = app_current_tenant());
-- These are deliberately the only production port for CS-061b mutable/auth-sensitive state.
CREATE FUNCTION auth_access_state(p_tenant_id uuid, p_user_id uuid, p_jti uuid, p_iat timestamptz)
RETURNS TABLE (role text, is_active boolean, tenant_active boolean, revoked boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT u.role, u.is_active, t.status = 'active',
   EXISTS (SELECT 1 FROM auth_revoked_tokens r WHERE r.jti=p_jti)
   OR (u.sessions_revoked_at IS NOT NULL AND p_iat < u.sessions_revoked_at)
 FROM tenant_users u JOIN tenants t ON t.id=u.tenant_id WHERE u.tenant_id=p_tenant_id AND u.id=p_user_id
$$;
CREATE FUNCTION auth_revoke_access_token(p_jti uuid,p_tenant_id uuid,p_user_id uuid,p_expires_at timestamptz)
RETURNS void LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 INSERT INTO auth_revoked_tokens(jti,tenant_id,user_id,expires_at) VALUES(p_jti,p_tenant_id,p_user_id,p_expires_at) ON CONFLICT (jti) DO NOTHING
$$;
CREATE FUNCTION auth_revoke_user_sessions(p_tenant_id uuid,p_user_id uuid,p_now timestamptz)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN UPDATE tenant_users SET sessions_revoked_at=p_now WHERE tenant_id=p_tenant_id AND id=p_user_id; IF NOT FOUND THEN RAISE EXCEPTION 'unknown principal'; END IF; UPDATE auth_refresh_tokens SET revoked_at=COALESCE(revoked_at,p_now) WHERE tenant_id=p_tenant_id AND user_id=p_user_id; END $$;
CREATE FUNCTION auth_issue_password_reset(p_tenant_id uuid,p_user_id uuid,p_digest text,p_expires_at timestamptz)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN UPDATE auth_password_reset_tokens SET consumed_at=COALESCE(consumed_at,now()) WHERE tenant_id=p_tenant_id AND user_id=p_user_id AND consumed_at IS NULL; INSERT INTO auth_password_reset_tokens(tenant_id,user_id,token_digest,expires_at) VALUES(p_tenant_id,p_user_id,p_digest,p_expires_at); END $$;
CREATE FUNCTION auth_consume_password_reset(p_digest text,p_password_hash text,p_now timestamptz)
RETURNS TABLE(tenant_id uuid,user_id uuid) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v auth_password_reset_tokens; BEGIN SELECT * INTO v FROM auth_password_reset_tokens r WHERE r.token_digest=p_digest FOR UPDATE; IF NOT FOUND OR v.consumed_at IS NOT NULL OR v.expires_at <= p_now THEN RETURN; END IF; UPDATE auth_password_reset_tokens r SET consumed_at=p_now WHERE r.id=v.id; UPDATE tenant_users u SET password_hash=p_password_hash,sessions_revoked_at=p_now WHERE u.tenant_id=v.tenant_id AND u.id=v.user_id; UPDATE auth_refresh_tokens r SET revoked_at=COALESCE(r.revoked_at,p_now) WHERE r.tenant_id=v.tenant_id AND r.user_id=v.user_id; RETURN QUERY SELECT v.tenant_id,v.user_id; END $$;
CREATE FUNCTION auth_record_login_attempt(p_tenant_id uuid,p_email text,p_ip inet,p_succeeded boolean,p_now timestamptz,p_max_failures integer,p_window_seconds integer)
RETURNS TABLE(locked boolean) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE account_failures int; ip_failures int; BEGIN IF p_max_failures <= 0 OR p_window_seconds <= 0 THEN RAISE EXCEPTION 'invalid throttle config'; END IF; PERFORM pg_advisory_xact_lock(hashtextextended(p_tenant_id::text || ':' || lower(p_email),0)); PERFORM pg_advisory_xact_lock(hashtextextended(p_tenant_id::text || ':ip:' || p_ip::text,0)); INSERT INTO auth_login_attempts(tenant_id,email,ip,succeeded,attempted_at) VALUES(p_tenant_id,p_email::citext,p_ip,p_succeeded,p_now); IF p_succeeded THEN DELETE FROM auth_login_attempts WHERE tenant_id=p_tenant_id AND email=p_email::citext AND attempted_at <= p_now; RETURN QUERY SELECT false; RETURN; END IF; SELECT count(*) INTO account_failures FROM auth_login_attempts WHERE tenant_id=p_tenant_id AND email=p_email::citext AND NOT succeeded AND attempted_at > p_now - make_interval(secs=>p_window_seconds); SELECT count(*) INTO ip_failures FROM auth_login_attempts WHERE tenant_id=p_tenant_id AND ip=p_ip AND NOT succeeded AND attempted_at > p_now - make_interval(secs=>p_window_seconds); RETURN QUERY SELECT account_failures >= p_max_failures OR ip_failures >= p_max_failures; END $$;
CREATE FUNCTION auth_login_locked(p_tenant_id uuid,p_email text,p_ip inet,p_now timestamptz,p_max_failures integer,p_window_seconds integer)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$ SELECT CASE WHEN p_max_failures <= 0 OR p_window_seconds <= 0 THEN true ELSE (SELECT count(*) >= p_max_failures FROM auth_login_attempts WHERE tenant_id=p_tenant_id AND email=p_email::citext AND NOT succeeded AND attempted_at > p_now - make_interval(secs=>p_window_seconds)) OR (SELECT count(*) >= p_max_failures FROM auth_login_attempts WHERE tenant_id=p_tenant_id AND ip=p_ip AND NOT succeeded AND attempted_at > p_now - make_interval(secs=>p_window_seconds)) END $$;
CREATE FUNCTION auth_store_mfa(p_tenant_id uuid,p_user_id uuid,p_cipher bytea,p_step bigint,p_recovery text[])
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN UPDATE tenant_users SET mfa_enabled=true,mfa_secret_ciphertext=p_cipher,mfa_last_timestep=p_step WHERE tenant_id=p_tenant_id AND id=p_user_id AND role IN ('owner', 'manager'); IF NOT FOUND THEN RAISE EXCEPTION 'mfa role forbidden'; END IF; DELETE FROM auth_mfa_recovery_codes WHERE tenant_id=p_tenant_id AND user_id=p_user_id; INSERT INTO auth_mfa_recovery_codes(tenant_id,user_id,code_digest) SELECT p_tenant_id,p_user_id,x FROM unnest(p_recovery) x; END $$;
CREATE FUNCTION auth_consume_mfa_step(p_tenant_id uuid,p_user_id uuid,p_step bigint)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN UPDATE tenant_users SET mfa_last_timestep=p_step WHERE tenant_id=p_tenant_id AND id=p_user_id AND mfa_enabled AND (mfa_last_timestep IS NULL OR mfa_last_timestep < p_step); RETURN FOUND; END $$;
CREATE FUNCTION auth_get_mfa(p_tenant_id uuid,p_user_id uuid)
RETURNS TABLE(ciphertext bytea,last_timestep bigint,recovery_digests text[]) LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT u.mfa_secret_ciphertext,u.mfa_last_timestep,coalesce(array_agg(r.code_digest) FILTER (WHERE r.consumed_at IS NULL),ARRAY[]::text[]) FROM tenant_users u LEFT JOIN auth_mfa_recovery_codes r ON r.tenant_id=u.tenant_id AND r.user_id=u.id WHERE u.tenant_id=p_tenant_id AND u.id=p_user_id AND u.mfa_enabled GROUP BY u.mfa_secret_ciphertext,u.mfa_last_timestep
$$;
CREATE FUNCTION auth_consume_recovery_code(p_tenant_id uuid,p_user_id uuid,p_digest text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$ BEGIN UPDATE auth_mfa_recovery_codes SET consumed_at=now() WHERE id=(SELECT id FROM auth_mfa_recovery_codes WHERE tenant_id=p_tenant_id AND user_id=p_user_id AND code_digest=p_digest AND consumed_at IS NULL FOR UPDATE); RETURN FOUND; END $$;
REVOKE ALL ON FUNCTION auth_access_state(uuid,uuid,uuid,timestamptz), auth_revoke_access_token(uuid,uuid,uuid,timestamptz), auth_revoke_user_sessions(uuid,uuid,timestamptz), auth_issue_password_reset(uuid,uuid,text,timestamptz), auth_consume_password_reset(text,text,timestamptz), auth_record_login_attempt(uuid,text,inet,boolean,timestamptz,integer,integer), auth_login_locked(uuid,text,inet,timestamptz,integer,integer), auth_store_mfa(uuid,uuid,bytea,bigint,text[]), auth_consume_mfa_step(uuid,uuid,bigint), auth_get_mfa(uuid,uuid), auth_consume_recovery_code(uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth_access_state(uuid,uuid,uuid,timestamptz), auth_revoke_access_token(uuid,uuid,uuid,timestamptz), auth_revoke_user_sessions(uuid,uuid,timestamptz), auth_issue_password_reset(uuid,uuid,text,timestamptz), auth_consume_password_reset(text,text,timestamptz), auth_record_login_attempt(uuid,text,inet,boolean,timestamptz,integer,integer), auth_login_locked(uuid,text,inet,timestamptz,integer,integer), auth_store_mfa(uuid,uuid,bytea,bigint,text[]), auth_consume_mfa_step(uuid,uuid,bigint), auth_get_mfa(uuid,uuid), auth_consume_recovery_code(uuid,uuid,text) TO auth_svc;
'''
def upgrade(): op.execute(SQL)
def downgrade():
    # Functions depend on the hardening relations; remove the precise overloaded
    # signatures first so downgrade is a true F43 reversible boundary.
    op.execute("""
    DROP FUNCTION IF EXISTS auth_consume_recovery_code(uuid,uuid,text);
    DROP FUNCTION IF EXISTS auth_get_mfa(uuid,uuid);
    DROP FUNCTION IF EXISTS auth_consume_mfa_step(uuid,uuid,bigint);
    DROP FUNCTION IF EXISTS auth_store_mfa(uuid,uuid,bytea,bigint,text[]);
    DROP FUNCTION IF EXISTS auth_login_locked(uuid,text,inet,timestamptz,integer,integer);
    DROP FUNCTION IF EXISTS auth_record_login_attempt(uuid,text,inet,boolean,timestamptz,integer,integer);
    DROP FUNCTION IF EXISTS auth_consume_password_reset(text,text,timestamptz);
    DROP FUNCTION IF EXISTS auth_issue_password_reset(uuid,uuid,text,timestamptz);
    DROP FUNCTION IF EXISTS auth_revoke_user_sessions(uuid,uuid,timestamptz);
    DROP FUNCTION IF EXISTS auth_revoke_access_token(uuid,uuid,uuid,timestamptz);
    DROP FUNCTION IF EXISTS auth_access_state(uuid,uuid,uuid,timestamptz);
    DROP TABLE IF EXISTS auth_login_attempts, auth_mfa_recovery_codes,
      auth_password_reset_tokens, auth_revoked_tokens;
    ALTER TABLE tenant_users DROP COLUMN IF EXISTS mfa_last_timestep,
      DROP COLUMN IF EXISTS mfa_secret_ciphertext, DROP COLUMN IF EXISTS mfa_enabled,
      DROP COLUMN IF EXISTS sessions_revoked_at;
    """)
