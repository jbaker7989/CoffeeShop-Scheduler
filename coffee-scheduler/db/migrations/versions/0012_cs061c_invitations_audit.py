"""CS-061c invitations and append-only audit log."""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

SQL = r'''
CREATE TABLE tenant_invitations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
 email citext NOT NULL, role text NOT NULL CHECK (role IN ('owner','manager','staff','read_only')),
 token_digest text NOT NULL UNIQUE, invited_by uuid NULL, status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','revoked','expired')),
 expires_at timestamptz NOT NULL, accepted_at timestamptz NULL, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (tenant_id, invited_by) REFERENCES tenant_users(tenant_id,id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX tenant_invitations_pending_email_unique ON tenant_invitations(tenant_id,email) WHERE status='pending';
CREATE TABLE audit_log (
 id bigserial PRIMARY KEY, tenant_id uuid NULL, actor_id uuid NULL,
 actor_type text NOT NULL CHECK (actor_type IN ('tenant_user','saas_admin','system')), action text NOT NULL,
 target_type text NULL, target_id uuid NULL, impersonated_by uuid NULL, before jsonb NULL, after jsonb NULL,
 ip inet NULL, user_agent text NULL, at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE tenant_invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenant_invitations USING (tenant_id=app_current_tenant());
CREATE POLICY tenant_isolation ON audit_log USING (tenant_id=app_current_tenant()) WITH CHECK (tenant_id=app_current_tenant());
GRANT INSERT, SELECT ON audit_log TO app_rw;
GRANT USAGE, SELECT ON SEQUENCE audit_log_id_seq TO app_rw;
CREATE FUNCTION auth_create_invitation(p_tenant_id uuid,p_email text,p_role text,p_token_digest text,p_invited_by uuid,p_expires_at timestamptz)
RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v_id uuid; BEGIN
 UPDATE tenant_invitations SET status='expired' WHERE tenant_id=p_tenant_id AND email=p_email::citext AND status='pending' AND expires_at<=now();
 IF EXISTS(SELECT 1 FROM tenant_users WHERE tenant_id=p_tenant_id AND email=p_email::citext) THEN RAISE EXCEPTION 'already a member'; END IF;
 IF EXISTS(SELECT 1 FROM tenant_invitations WHERE tenant_id=p_tenant_id AND email=p_email::citext AND status='pending') THEN RAISE EXCEPTION 'pending invitation exists'; END IF;
 INSERT INTO tenant_invitations(tenant_id,email,role,token_digest,invited_by,expires_at) VALUES(p_tenant_id,p_email::citext,p_role,p_token_digest,p_invited_by,p_expires_at) RETURNING id INTO v_id; RETURN v_id; END $$;
CREATE FUNCTION auth_get_invitation(p_token_digest text)
RETURNS TABLE(id uuid,tenant_id uuid,email citext,role text,token_digest text,invited_by uuid,status text,expires_at timestamptz,accepted_at timestamptz,created_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$ SELECT i.id,i.tenant_id,i.email,i.role,i.token_digest,i.invited_by,i.status,i.expires_at,i.accepted_at,i.created_at FROM tenant_invitations i WHERE i.token_digest=p_token_digest $$;
CREATE FUNCTION auth_accept_invitation(p_token_digest text,p_password_hash text,p_now timestamptz)
RETURNS TABLE(tenant_id uuid,user_id uuid,role text) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v tenant_invitations; v_user uuid; BEGIN SELECT * INTO v FROM tenant_invitations WHERE token_digest=p_token_digest FOR UPDATE;
 IF NOT FOUND OR v.status <> 'pending' OR v.expires_at <= p_now OR EXISTS(SELECT 1 FROM tenant_users WHERE tenant_id=v.tenant_id AND email=v.email) THEN RETURN; END IF;
 INSERT INTO tenant_users(tenant_id,email,password_hash,role) VALUES(v.tenant_id,v.email,p_password_hash,v.role) RETURNING id INTO v_user;
 UPDATE tenant_invitations SET status='accepted',accepted_at=p_now WHERE id=v.id;
 RETURN QUERY SELECT v.tenant_id,v_user,v.role; EXCEPTION WHEN unique_violation THEN RETURN; END $$;
CREATE FUNCTION auth_list_invitations(p_tenant_id uuid)
RETURNS TABLE(id uuid,tenant_id uuid,email citext,role text,token_digest text,invited_by uuid,status text,expires_at timestamptz,accepted_at timestamptz,created_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$ SELECT i.id,i.tenant_id,i.email,i.role,i.token_digest,i.invited_by,CASE WHEN i.status='pending' AND i.expires_at<=now() THEN 'expired' ELSE i.status END,i.expires_at,i.accepted_at,i.created_at FROM tenant_invitations i WHERE i.tenant_id=p_tenant_id ORDER BY (i.status='pending' AND i.expires_at>now()) DESC,i.created_at DESC $$;
CREATE FUNCTION auth_revoke_invitation(p_tenant_id uuid,p_invitation_id uuid)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$ BEGIN UPDATE tenant_invitations SET status='revoked' WHERE tenant_id=p_tenant_id AND id=p_invitation_id AND status='pending'; RETURN FOUND; END $$;
CREATE FUNCTION auth_record_audit(p_tenant_id uuid,p_actor_id uuid,p_actor_type text,p_action text,p_target_type text,p_target_id uuid,p_impersonated_by uuid,p_before jsonb,p_after jsonb,p_ip inet,p_user_agent text)
RETURNS bigint LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS $$ INSERT INTO audit_log(tenant_id,actor_id,actor_type,action,target_type,target_id,impersonated_by,before,after,ip,user_agent) VALUES(p_tenant_id,p_actor_id,p_actor_type,p_action,p_target_type,p_target_id,p_impersonated_by,p_before,p_after,p_ip,p_user_agent) RETURNING id $$;
REVOKE ALL ON FUNCTION auth_create_invitation(uuid,text,text,text,uuid,timestamptz),auth_get_invitation(text),auth_accept_invitation(text,text,timestamptz),auth_list_invitations(uuid),auth_revoke_invitation(uuid,uuid),auth_record_audit(uuid,uuid,text,text,text,uuid,uuid,jsonb,jsonb,inet,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth_create_invitation(uuid,text,text,text,uuid,timestamptz),auth_get_invitation(text),auth_accept_invitation(text,text,timestamptz),auth_list_invitations(uuid),auth_revoke_invitation(uuid,uuid),auth_record_audit(uuid,uuid,text,text,text,uuid,uuid,jsonb,jsonb,inet,text) TO auth_svc;
'''
def upgrade(): op.execute(SQL)
def downgrade():
    op.execute('''
DROP FUNCTION IF EXISTS auth_record_audit(uuid,uuid,text,text,text,uuid,uuid,jsonb,jsonb,inet,text);
DROP FUNCTION IF EXISTS auth_revoke_invitation(uuid,uuid);
DROP FUNCTION IF EXISTS auth_list_invitations(uuid);
DROP FUNCTION IF EXISTS auth_accept_invitation(text,text,timestamptz);
DROP FUNCTION IF EXISTS auth_get_invitation(text);
DROP FUNCTION IF EXISTS auth_create_invitation(uuid,text,text,text,uuid,timestamptz);
DROP TABLE IF EXISTS audit_log, tenant_invitations;
''')
