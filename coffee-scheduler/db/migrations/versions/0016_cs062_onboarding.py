"""CS-062 tenant self-service onboarding and trial access."""
from alembic import op


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


# These functions are the only write port for tenant creation.  In particular, do
# not grant ``app_rw`` DML on tenants or tenant_users: anonymous signup has no
# tenant context and tenant creation is deliberately a narrowly owned operation.
SQL = r'''
ALTER TABLE tenants ADD COLUMN trial_ends_at TIMESTAMPTZ NULL,
                    ADD COLUMN provisioned_at TIMESTAMPTZ NULL;
ALTER TABLE tenant_users ADD COLUMN email_verified_at TIMESTAMPTZ NULL;

CREATE TABLE onboarding_verification_tokens (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id uuid NOT NULL,
 user_id uuid NOT NULL,
 token_digest text NOT NULL UNIQUE,
 expires_at timestamptz NOT NULL,
 consumed_at timestamptz NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_users(tenant_id, id) ON DELETE CASCADE
);
ALTER TABLE onboarding_verification_tokens ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON onboarding_verification_tokens
 USING (tenant_id = app_current_tenant()) WITH CHECK (tenant_id = app_current_tenant());

-- The registry is deliberately global: it is the reviewed source for a jurisdiction,
-- not tenant data. ``jurisdiction`` is the tenant-facing key (NY-STATE+NYC); the
-- individual legal jurisdiction remains in ``rule_jurisdiction``.
CREATE TABLE labor_rule_templates (
 jurisdiction text NOT NULL,
 rule_id text NOT NULL,
 rule_category text,
 rule_jurisdiction text,
 wage_order text,
 enforcement text,
 applies_age_min int,
 applies_age_max int,
 applies_school_status text,
 applies_day_type text,
 applies_season text,
 requires_employer_flag text,
 metric text,
 operator text,
 value_num numeric,
 value_time time,
 value_text text,
 unit text,
 consequence text,
 waivable_by_consent boolean,
 waiver_doc_required boolean,
 priority int,
 effective_from date NOT NULL,
 effective_to date,
 source_citation text,
 source_url text,
 verification_status text,
 verified_on date,
 notes text,
 PRIMARY KEY (jurisdiction, rule_id, effective_from)
);

-- Snapshot the already verified Maven NY set once.  The tenant-facing jurisdiction
-- is composite, while each copied rule retains its source legal jurisdiction.
INSERT INTO labor_rule_templates (
 jurisdiction, rule_id, rule_category, rule_jurisdiction, wage_order, enforcement,
 applies_age_min, applies_age_max, applies_school_status, applies_day_type,
 applies_season, requires_employer_flag, metric, operator, value_num, value_time,
 value_text, unit, consequence, waivable_by_consent, waiver_doc_required, priority,
 effective_from, effective_to, source_citation, source_url, verification_status,
 verified_on, notes
)
SELECT 'NY-STATE+NYC', r.rule_id, r.rule_category, r.jurisdiction, r.wage_order,
 r.enforcement, r.applies_age_min, r.applies_age_max, r.applies_school_status,
 r.applies_day_type, r.applies_season, r.requires_employer_flag, r.metric, r.operator,
 r.value_num, r.value_time, r.value_text, r.unit, r.consequence,
 r.waivable_by_consent, r.waiver_doc_required, r.priority, r.effective_from,
 r.effective_to, r.source_citation, r.source_url, r.verification_status,
 r.verified_on, r.notes
FROM labor_rules r JOIN tenants t ON t.id = r.tenant_id
WHERE t.slug = 'maven-demo'
  AND r.jurisdiction IN ('US-FED', 'NY-STATE', 'NYC')
  AND r.deleted_at IS NULL;

CREATE FUNCTION onboarding_signup(
 p_slug text, p_name text, p_jurisdiction text, p_timezone text,
 p_owner_email text, p_owner_password_hash text, p_trial_days integer, p_now timestamptz
) RETURNS TABLE(tenant_id uuid, user_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_tenant_id uuid; v_user_id uuid;
BEGIN
 IF p_trial_days <= 0 THEN RAISE EXCEPTION 'invalid trial days'; END IF;
 INSERT INTO tenants(slug,name,status,jurisdiction,timezone,trial_ends_at)
 VALUES(p_slug,p_name,'trial',p_jurisdiction,p_timezone,p_now + make_interval(days => p_trial_days))
 RETURNING id INTO v_tenant_id;
 INSERT INTO tenant_users(tenant_id,email,password_hash,role)
 VALUES(v_tenant_id,p_owner_email::citext,p_owner_password_hash,'owner') RETURNING id INTO v_user_id;
 RETURN QUERY SELECT v_tenant_id,v_user_id;
END $$;

CREATE FUNCTION onboarding_issue_verification(
 p_tenant_id uuid, p_user_id uuid, p_digest text, p_expires_at timestamptz
) RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
 UPDATE onboarding_verification_tokens SET consumed_at=COALESCE(consumed_at,now())
 WHERE tenant_id=p_tenant_id AND user_id=p_user_id AND consumed_at IS NULL;
 INSERT INTO onboarding_verification_tokens(tenant_id,user_id,token_digest,expires_at)
 VALUES(p_tenant_id,p_user_id,p_digest,p_expires_at);
END $$;

CREATE FUNCTION onboarding_verify_email(p_digest text, p_now timestamptz)
RETURNS TABLE(tenant_id uuid, user_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v onboarding_verification_tokens;
BEGIN
 SELECT * INTO v FROM onboarding_verification_tokens
 WHERE token_digest=p_digest FOR UPDATE;
 IF NOT FOUND OR v.consumed_at IS NOT NULL OR v.expires_at <= p_now THEN RETURN; END IF;
 UPDATE tenant_users SET email_verified_at=COALESCE(email_verified_at,p_now)
 WHERE tenant_id=v.tenant_id AND id=v.user_id;
 UPDATE onboarding_verification_tokens SET consumed_at=p_now WHERE id=v.id;
 RETURN QUERY SELECT v.tenant_id,v.user_id;
END $$;

CREATE FUNCTION onboarding_email_verified(p_tenant_id uuid, p_user_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT EXISTS(SELECT 1 FROM tenant_users WHERE tenant_id=p_tenant_id AND id=p_user_id
               AND email_verified_at IS NOT NULL)
$$;

CREATE FUNCTION onboarding_tenant_jurisdiction(p_tenant_id uuid)
RETURNS text LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT jurisdiction FROM tenants WHERE id=p_tenant_id
$$;

CREATE FUNCTION onboarding_suspend_expired_trials(p_now timestamptz)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_count integer;
BEGIN
 UPDATE tenants SET status='suspended'
 WHERE status='trial' AND trial_ends_at IS NOT NULL AND trial_ends_at <= p_now;
 GET DIAGNOSTICS v_count = ROW_COUNT;
 RETURN v_count;
END $$;

CREATE FUNCTION onboarding_provision(p_tenant_id uuid, p_jurisdiction text, p_now timestamptz)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
 PERFORM 1 FROM tenants WHERE id=p_tenant_id AND provisioned_at IS NOT NULL FOR UPDATE;
 IF FOUND THEN RETURN false; END IF;
 PERFORM 1 FROM tenants WHERE id=p_tenant_id FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'unknown tenant'; END IF;
 IF NOT EXISTS (SELECT 1 FROM labor_rule_templates WHERE jurisdiction=p_jurisdiction) THEN
   RAISE EXCEPTION 'jurisdiction has no verified rule set';
 END IF;
 INSERT INTO labor_rules(
 tenant_id,rule_id,rule_category,jurisdiction,wage_order,enforcement,applies_age_min,
 applies_age_max,applies_school_status,applies_day_type,applies_season,requires_employer_flag,
 metric,operator,value_num,value_time,value_text,unit,consequence,waivable_by_consent,
 waiver_doc_required,priority,effective_from,effective_to,source_citation,source_url,
 verification_status,verified_on,notes
 ) SELECT p_tenant_id,rule_id,rule_category,rule_jurisdiction,wage_order,enforcement,
 applies_age_min,applies_age_max,applies_school_status,applies_day_type,applies_season,
 requires_employer_flag,metric,operator,value_num,value_time,value_text,unit,consequence,
 waivable_by_consent,waiver_doc_required,priority,effective_from,effective_to,source_citation,
 source_url,verification_status,verified_on,notes
 FROM labor_rule_templates WHERE jurisdiction=p_jurisdiction;
 -- These are the standards that are meaningful before the deferred store wizard.
 INSERT INTO throughput_standards(tenant_id,product_category,prep_role,items_sold_jan_jun_2023,
 seconds_per_unit,units_per_labor_hour,basis,notes)
 SELECT p_tenant_id,s.product_category,s.prep_role,s.items_sold_jan_jun_2023,
 s.seconds_per_unit,s.units_per_labor_hour,s.basis,s.notes
 FROM throughput_standards s JOIN tenants source_tenant ON source_tenant.id=s.tenant_id
 WHERE source_tenant.slug='maven-demo' AND s.deleted_at IS NULL;
 -- Cost targets depend on a store FK.  If a later wizard has created matching stores,
 -- seed them then; this initial provisioning deliberately never invents a store.
 INSERT INTO cost_targets(tenant_id,location_id,store_location,target_labor_pct_of_sales,
 weekly_overtime_budget_usd,understaffing_penalty_weight,premium_penalty_weight,basis)
 SELECT p_tenant_id,c.location_id,c.store_location,c.target_labor_pct_of_sales,
 c.weekly_overtime_budget_usd,c.understaffing_penalty_weight,c.premium_penalty_weight,c.basis
 FROM cost_targets c JOIN tenants source_tenant ON source_tenant.id=c.tenant_id
 JOIN stores target_store ON target_store.tenant_id=p_tenant_id AND target_store.location_id=c.location_id
 WHERE source_tenant.slug='maven-demo' AND c.deleted_at IS NULL;
 UPDATE tenants SET provisioned_at=p_now WHERE id=p_tenant_id;
 RETURN true;
END $$;

CREATE OR REPLACE FUNCTION auth_access_state(p_tenant_id uuid, p_user_id uuid, p_jti uuid, p_iat timestamptz)
RETURNS TABLE (role text, is_active boolean, tenant_active boolean, revoked boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT u.role, u.is_active, t.status IN ('active','trial'),
   EXISTS (SELECT 1 FROM auth_revoked_tokens r WHERE r.jti=p_jti)
   OR (u.sessions_revoked_at IS NOT NULL AND p_iat < u.sessions_revoked_at)
 FROM tenant_users u JOIN tenants t ON t.id=u.tenant_id WHERE u.tenant_id=p_tenant_id AND u.id=p_user_id
$$;

REVOKE ALL ON FUNCTION onboarding_signup(text,text,text,text,text,text,integer,timestamptz),
 onboarding_issue_verification(uuid,uuid,text,timestamptz), onboarding_verify_email(text,timestamptz),
 onboarding_email_verified(uuid,uuid), onboarding_tenant_jurisdiction(uuid), onboarding_suspend_expired_trials(timestamptz),
 onboarding_provision(uuid,text,timestamptz), auth_access_state(uuid,uuid,uuid,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION onboarding_signup(text,text,text,text,text,text,integer,timestamptz),
 onboarding_issue_verification(uuid,uuid,text,timestamptz), onboarding_verify_email(text,timestamptz),
 onboarding_email_verified(uuid,uuid), onboarding_tenant_jurisdiction(uuid), onboarding_suspend_expired_trials(timestamptz),
 onboarding_provision(uuid,text,timestamptz), auth_access_state(uuid,uuid,uuid,timestamptz) TO auth_svc;
'''


def upgrade() -> None:
    op.execute(SQL)


def downgrade() -> None:
    op.execute(r'''
DROP FUNCTION IF EXISTS onboarding_provision(uuid,text,timestamptz);
DROP FUNCTION IF EXISTS onboarding_suspend_expired_trials(timestamptz);
DROP FUNCTION IF EXISTS onboarding_email_verified(uuid,uuid);
DROP FUNCTION IF EXISTS onboarding_tenant_jurisdiction(uuid);
DROP FUNCTION IF EXISTS onboarding_verify_email(text,timestamptz);
DROP FUNCTION IF EXISTS onboarding_issue_verification(uuid,uuid,text,timestamptz);
DROP FUNCTION IF EXISTS onboarding_signup(text,text,text,text,text,text,integer,timestamptz);
DROP TABLE IF EXISTS onboarding_verification_tokens;
DROP TABLE IF EXISTS labor_rule_templates;
ALTER TABLE tenant_users DROP COLUMN IF EXISTS email_verified_at;
ALTER TABLE tenants DROP COLUMN IF EXISTS provisioned_at, DROP COLUMN IF EXISTS trial_ends_at;
CREATE OR REPLACE FUNCTION auth_access_state(p_tenant_id uuid, p_user_id uuid, p_jti uuid, p_iat timestamptz)
RETURNS TABLE (role text, is_active boolean, tenant_active boolean, revoked boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT u.role, u.is_active, t.status = 'active',
   EXISTS (SELECT 1 FROM auth_revoked_tokens r WHERE r.jti=p_jti)
   OR (u.sessions_revoked_at IS NOT NULL AND p_iat < u.sessions_revoked_at)
 FROM tenant_users u JOIN tenants t ON t.id=u.tenant_id WHERE u.tenant_id=p_tenant_id AND u.id=p_user_id
$$;
REVOKE ALL ON FUNCTION auth_access_state(uuid,uuid,uuid,timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth_access_state(uuid,uuid,uuid,timestamptz) TO auth_svc;
''')
