"""BUG-041: qualify invitation acceptance queries to avoid PL/pgSQL OUT-name ambiguity."""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


FIXED_FUNCTION = r'''
CREATE OR REPLACE FUNCTION auth_accept_invitation(
    p_token_digest text,
    p_password_hash text,
    p_now timestamptz
)
RETURNS TABLE(tenant_id uuid, user_id uuid, role text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    v_invitation public.tenant_invitations;
    v_user_id uuid;
BEGIN
    SELECT i.* INTO v_invitation
      FROM public.tenant_invitations AS i
     WHERE i.token_digest = p_token_digest
     FOR UPDATE;
    IF NOT FOUND
       OR v_invitation.status <> 'pending'
       OR v_invitation.expires_at <= p_now
       OR EXISTS (
           SELECT 1
             FROM public.tenant_users AS u
            WHERE u.tenant_id = v_invitation.tenant_id
              AND u.email = v_invitation.email
       ) THEN
        RETURN;
    END IF;

    INSERT INTO public.tenant_users(tenant_id, email, password_hash, role)
    VALUES (v_invitation.tenant_id, v_invitation.email, p_password_hash, v_invitation.role)
    RETURNING id INTO v_user_id;
    UPDATE public.tenant_invitations AS i
       SET status = 'accepted', accepted_at = p_now
     WHERE i.id = v_invitation.id;
    RETURN QUERY SELECT v_invitation.tenant_id, v_user_id, v_invitation.role;
EXCEPTION WHEN unique_violation THEN
    RETURN;
END
$$;
'''


PREVIOUS_FUNCTION = r'''
CREATE OR REPLACE FUNCTION auth_accept_invitation(p_token_digest text,p_password_hash text,p_now timestamptz)
RETURNS TABLE(tenant_id uuid,user_id uuid,role text) LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE v tenant_invitations; v_user uuid; BEGIN SELECT * INTO v FROM tenant_invitations WHERE token_digest=p_token_digest FOR UPDATE;
 IF NOT FOUND OR v.status <> 'pending' OR v.expires_at <= p_now OR EXISTS(SELECT 1 FROM tenant_users WHERE tenant_id=v.tenant_id AND email=v.email) THEN RETURN; END IF;
 INSERT INTO tenant_users(tenant_id,email,password_hash,role) VALUES(v.tenant_id,v.email,p_password_hash,v.role) RETURNING id INTO v_user;
 UPDATE tenant_invitations SET status='accepted',accepted_at=p_now WHERE id=v.id;
 RETURN QUERY SELECT v.tenant_id,v_user,v.role; EXCEPTION WHEN unique_violation THEN RETURN; END $$;
'''


def upgrade() -> None:
    op.execute(FIXED_FUNCTION)


def downgrade() -> None:
    op.execute(PREVIOUS_FUNCTION)
