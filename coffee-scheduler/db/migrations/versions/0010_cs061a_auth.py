"""CS-061a / F61 — core tenant authentication identities and refresh-token families.

Adds the first authenticated tenant boundary on top of F60's RLS/session-context
foundation (see `docs/sdd-cs061a.md`). Three relations:

- ``tenant_users``       — tenant-local login identities (case-insensitive email,
                           allowlisted role, Argon2id ``password_hash``, active flag).
- ``saas_admins``        — a *separate* global admin identity population. No login,
                           impersonation, or MFA provisioning is created here; the
                           ``mfa_secret_ciphertext`` column stores only encrypted MFA
                           material (CS-061b owns enrolment), never a raw secret.
- ``auth_refresh_tokens``— server-side refresh-token rotation authority, keyed by a
                           one-way ``token_digest`` (never the raw token), with family
                           grouping and rotation/revocation timestamps.

## Access model (review BUG fixes to the first draft of this migration)

Anonymous login starts with only ``(tenant_slug, email)`` and refresh starts with only an
opaque token — **before** any trusted tenant is known. FORCE RLS keyed on
``app_current_tenant()`` therefore cannot service these flows (it returns zero rows), and
granting ``app_rw`` direct ``SELECT`` on these tables would expose every same-tenant
``password_hash`` / ``token_digest`` to ordinary application code.

So credential access is **not** via direct table privileges, and not from general app
roles at all. No role has DML on ``tenant_users`` / ``auth_refresh_tokens``. A small set of
``SECURITY DEFINER`` functions (``auth_*``) encapsulate exactly the parameterised queries
the auth service needs; each pins ``search_path`` (so a caller cannot inject object
resolution) and has its default ``PUBLIC`` execute privilege revoked. ``EXECUTE`` is granted
**only** to a dedicated ``auth_svc`` role — the trusted auth-service port — because
``auth_find_tenant_user`` returns a password hash for any ``(slug, email)`` and must never
be reachable from ``app_rw``/``app_ro`` (the general data-access layer's roles).

Because those functions must resolve a login/token *before* a tenant is known, they run as
the table owner and the two auth tables use ``ENABLE`` (not ``FORCE``) RLS — the owner then
bypasses the policy, exactly as the ``tenants`` registry does in 0004 for the same
resolve-by-slug reason. The ``tenant_isolation`` policy is retained as defense-in-depth for
any future non-owner grant; with no such grant today it is belt-and-suspenders.

``saas_admins`` has no ``tenant_id`` (spec AC1); isolation is by grant — only ``secrets_rw``
may touch it, as ``employee_pii`` is restricted in 0001.

Cross-tenant integrity: ``auth_refresh_tokens`` carries both ``tenant_id`` and ``user_id``,
so a composite FK ``(tenant_id, user_id) -> tenant_users(tenant_id, id)`` (backed by a
``UNIQUE (tenant_id, id)`` on ``tenant_users``) guarantees the token's user actually belongs
to the token's tenant; ``tenant_users.tenant_id -> tenants(id)`` closes the chain.

Revision ID: 0010
Revises: 0009
"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

# The two tenant-scoped auth relations keep a tenant_isolation policy as defense in
# depth, but use ENABLE (not FORCE) so the owner-run SECURITY DEFINER auth_* functions
# can resolve an anonymous login/refresh (see the module docstring + 0004 precedent).
AUTH_TENANT_TABLES = ["tenant_users", "auth_refresh_tokens"]

# EXECUTE on these is granted to the dedicated auth_svc role only; PUBLIC execute is
# revoked. app_rw/app_ro (the general data-access roles) get nothing here, because
# auth_find_tenant_user returns password hashes for any (slug, email).
AUTH_SERVICE_ROLE = "auth_svc"
AUTH_FUNCTION_SIGNATURES = [
    "auth_find_tenant_user(text, text)",
    "auth_get_tenant_user_by_id(uuid, uuid)",
    "auth_record_login(uuid, timestamptz)",
    "auth_issue_refresh_token(uuid, uuid, uuid, uuid, text, timestamptz, timestamptz)",
    "auth_get_refresh_token(text)",
    "auth_consume_refresh_token(text, uuid, text, timestamptz, timestamptz, timestamptz)",
    "auth_revoke_refresh_family(uuid, timestamptz)",
]

# Uppercase SQL types here match the CS-061a contract (docs/sdd-cs061a.md, AC1 table).
TENANT_USERS = """
CREATE TABLE tenant_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email CITEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'manager', 'staff', 'read_only')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ NULL,
    UNIQUE (tenant_id, email),
    UNIQUE (tenant_id, id)
)
"""

# mfa_secret_ciphertext holds only encrypted MFA material; a raw global secret would be
# unsafe even while unused, so no plaintext column exists. Enrolment is CS-061b.
SAAS_ADMINS = """
CREATE TABLE saas_admins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email CITEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    mfa_secret_ciphertext BYTEA NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# token_digest is a one-way digest of the opaque refresh secret (never the secret).
# The composite FK guarantees user_id belongs to tenant_id.
AUTH_REFRESH_TOKENS = """
CREATE TABLE auth_refresh_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    user_id UUID NOT NULL,
    token_digest TEXT NOT NULL UNIQUE,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    rotated_at TIMESTAMPTZ NULL,
    revoked_at TIMESTAMPTZ NULL,
    FOREIGN KEY (tenant_id, user_id) REFERENCES tenant_users (tenant_id, id) ON DELETE CASCADE
)
"""

# ---- SECURITY DEFINER auth operations (the only app_rw access to credential rows) ----
# Every function pins search_path to a fixed value (pg_catalog first so built-ins cannot
# be shadowed; public second so the citext operators resolve) and runs as the owner.

FN_FIND_TENANT_USER = """
CREATE FUNCTION auth_find_tenant_user(p_tenant_slug text, p_email text)
RETURNS TABLE (
    id uuid, tenant_id uuid, tenant_slug text, email text,
    password_hash text, role text, is_active boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT u.id, u.tenant_id, t.slug, u.email::text, u.password_hash, u.role, u.is_active
    FROM tenant_users u JOIN tenants t ON t.id = u.tenant_id
    WHERE t.slug = p_tenant_slug AND u.email = p_email::citext
$$
"""

# Canonical membership reconciliation (F61.ac2 strict tenant-claim authority). Returns
# ONLY non-secret identity fields — no password_hash, email, or token digest — so it
# cannot be used to harvest credentials even though it is EXECUTE-granted to auth_svc.
FN_GET_TENANT_USER_BY_ID = """
CREATE FUNCTION auth_get_tenant_user_by_id(p_tenant_id uuid, p_user_id uuid)
RETURNS TABLE (id uuid, tenant_id uuid, role text, is_active boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT u.id, u.tenant_id, u.role, u.is_active
    FROM tenant_users u
    WHERE u.id = p_user_id AND u.tenant_id = p_tenant_id
$$
"""

FN_RECORD_LOGIN = """
CREATE FUNCTION auth_record_login(p_user_id uuid, p_when timestamptz)
RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    UPDATE tenant_users SET last_login_at = COALESCE(p_when, now()) WHERE id = p_user_id
$$
"""

FN_ISSUE_REFRESH = """
CREATE FUNCTION auth_issue_refresh_token(
    p_id uuid, p_family_id uuid, p_tenant_id uuid, p_user_id uuid,
    p_token_digest text, p_issued_at timestamptz, p_expires_at timestamptz)
RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    INSERT INTO auth_refresh_tokens
        (id, family_id, tenant_id, user_id, token_digest, issued_at, expires_at)
    VALUES (p_id, p_family_id, p_tenant_id, p_user_id, p_token_digest, p_issued_at, p_expires_at)
$$
"""

FN_GET_REFRESH = """
CREATE FUNCTION auth_get_refresh_token(p_token_digest text)
RETURNS SETOF auth_refresh_tokens
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT * FROM auth_refresh_tokens WHERE token_digest = p_token_digest
$$
"""

# One atomic unit: single-use the presented token, and either rotate (issue the child) or,
# on reuse/revocation, burn the whole family. The child's family_id/tenant_id/user_id are
# DERIVED from the locked parent (never caller-supplied), so an application-layer bug can
# never re-home a refresh family to another tenant or user. Returns the status plus the
# canonical family/tenant/user the service needs to mint the next access token.
FN_CONSUME_REFRESH = """
CREATE FUNCTION auth_consume_refresh_token(
    p_presented_digest text,
    p_child_id uuid, p_child_token_digest text,
    p_child_issued_at timestamptz, p_child_expires_at timestamptz,
    p_now timestamptz)
RETURNS TABLE (status text, family_id uuid, tenant_id uuid, user_id uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
-- The RETURNS TABLE output names (family_id, ...) shadow the table columns; resolve any
-- bare reference to the column so the family-burn UPDATE is unambiguous. OUT columns are
-- only ever written positionally via RETURN QUERY, never read by name, so this is safe.
#variable_conflict use_column
DECLARE
    v auth_refresh_tokens;
BEGIN
    SELECT * INTO v FROM auth_refresh_tokens
        WHERE token_digest = p_presented_digest FOR UPDATE;
    IF NOT FOUND THEN
        RETURN QUERY SELECT 'NOT_FOUND'::text, NULL::uuid, NULL::uuid, NULL::uuid;
        RETURN;
    END IF;
    IF v.revoked_at IS NOT NULL THEN
        UPDATE auth_refresh_tokens SET revoked_at = COALESCE(revoked_at, p_now)
            WHERE family_id = v.family_id AND revoked_at IS NULL;
        RETURN QUERY SELECT 'REVOKED'::text, v.family_id, v.tenant_id, v.user_id;
        RETURN;
    END IF;
    IF v.rotated_at IS NOT NULL THEN
        UPDATE auth_refresh_tokens SET revoked_at = COALESCE(revoked_at, p_now)
            WHERE family_id = v.family_id AND revoked_at IS NULL;
        RETURN QUERY SELECT 'REPLAY'::text, v.family_id, v.tenant_id, v.user_id;
        RETURN;
    END IF;
    IF v.expires_at <= p_now THEN
        RETURN QUERY SELECT 'EXPIRED'::text, v.family_id, v.tenant_id, v.user_id;
        RETURN;
    END IF;
    UPDATE auth_refresh_tokens SET rotated_at = p_now WHERE id = v.id;
    INSERT INTO auth_refresh_tokens
        (id, family_id, tenant_id, user_id, token_digest, issued_at, expires_at)
    VALUES (p_child_id, v.family_id, v.tenant_id, v.user_id,
            p_child_token_digest, p_child_issued_at, p_child_expires_at);
    RETURN QUERY SELECT 'ROTATED'::text, v.family_id, v.tenant_id, v.user_id;
END;
$$
"""

FN_REVOKE_FAMILY = """
CREATE FUNCTION auth_revoke_refresh_family(p_family_id uuid, p_now timestamptz)
RETURNS integer
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    WITH upd AS (
        UPDATE auth_refresh_tokens SET revoked_at = COALESCE(revoked_at, p_now)
        WHERE family_id = p_family_id AND revoked_at IS NULL
        RETURNING 1)
    SELECT count(*)::int FROM upd
$$
"""

AUTH_FUNCTIONS = [
    FN_FIND_TENANT_USER, FN_GET_TENANT_USER_BY_ID, FN_RECORD_LOGIN, FN_ISSUE_REFRESH,
    FN_GET_REFRESH, FN_CONSUME_REFRESH, FN_REVOKE_FAMILY,
]


def upgrade() -> None:
    conn = op.get_bind()

    # CITEXT powers tenant-local case-insensitive email uniqueness.
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    # Dedicated trusted-service role: the auth service connects as this (never as app_rw),
    # so the credential-returning auth_* functions are not reachable from general app code.
    # NOLOGIN like the other 0001 roles; CREATE ROLE has no IF NOT EXISTS.
    exists = conn.exec_driver_sql(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", (AUTH_SERVICE_ROLE,)
    ).scalar()
    if not exists:
        # Literal DDL (not an f-string) so the role grant is greppable in the migration and
        # matches the CS-061a contract; AUTH_SERVICE_ROLE below is this same "auth_svc".
        op.execute("CREATE ROLE auth_svc NOLOGIN")

    op.execute(TENANT_USERS)
    op.execute(SAAS_ADMINS)
    op.execute(AUTH_REFRESH_TOKENS)

    # Lookups the auth service performs: rotate-by-family and list-by-user.
    op.execute("CREATE INDEX auth_refresh_tokens_family_idx ON auth_refresh_tokens (family_id)")
    op.execute("CREATE INDEX auth_refresh_tokens_user_idx ON auth_refresh_tokens (tenant_id, user_id)")

    # ENABLE (not FORCE) + policy: defense in depth only. The owner (hence the auth_*
    # SECURITY DEFINER functions) bypasses it to resolve anonymous login/refresh.
    for t in AUTH_TENANT_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} "
            "USING (tenant_id = app_current_tenant()) "
            "WITH CHECK (tenant_id = app_current_tenant())"
        )

    # Create the credential-access functions, then lock them down: no PUBLIC execute,
    # auth_svc-only execute. No role has direct DML on the credential tables.
    for fn in AUTH_FUNCTIONS:
        op.execute(fn)
    for sig in AUTH_FUNCTION_SIGNATURES:
        op.execute(f"REVOKE ALL ON FUNCTION {sig} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {sig} TO {AUTH_SERVICE_ROLE}")

    # saas_admins: global-but-sensitive, no tenant dimension -> isolation by grant,
    # not RLS. Only the privileged secrets role may touch admin credentials.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON saas_admins TO secrets_rw")


def downgrade() -> None:
    for sig in AUTH_FUNCTION_SIGNATURES:
        op.execute(f"DROP FUNCTION IF EXISTS {sig}")
    # Children before parents: auth_refresh_tokens references tenant_users.
    # CASCADE removes the policies and indexes with the tables.
    for t in ("auth_refresh_tokens", "saas_admins", "tenant_users"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")

    # Drop the trusted-service role last: its only dependencies were the EXECUTE grants
    # on the functions just dropped. DROP OWNED clears any residual grant before DROP ROLE.
    conn = op.get_bind()
    exists = conn.exec_driver_sql(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", (AUTH_SERVICE_ROLE,)
    ).scalar()
    if exists:
        op.execute(f"DROP OWNED BY {AUTH_SERVICE_ROLE}")
        op.execute(f"DROP ROLE {AUTH_SERVICE_ROLE}")
    # The citext extension is intentionally left in place: extensions are shared
    # infrastructure (0001 likewise does not drop btree_gist on downgrade).
