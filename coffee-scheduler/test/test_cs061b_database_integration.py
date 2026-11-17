"""Opt-in real PostgreSQL proof for CS-061b auth_svc-only persistence.

Run after ``alembic upgrade head`` with ``DATABASE_URL`` set to an owner URL.
The test deliberately opens a fresh auth-service connection after every mutation.
"""
import os, unittest, uuid, subprocess, sys
from datetime import datetime, timezone, timedelta
import psycopg

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
@unittest.skipUnless(os.environ.get("CS061B_DATABASE_INTEGRATION"), "set CS061B_DATABASE_INTEGRATION=1")
class CS061bDatabaseIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Explicit paths make this executable from any working directory.
        result=subprocess.run(["alembic","-c",os.path.join(ROOT,"db","alembic.ini"),"upgrade","head"],cwd=os.path.join(ROOT,"db"),env=os.environ.copy(),capture_output=True,text=True)
        if result.returncode: raise RuntimeError(result.stdout+result.stderr)
    def setUp(self):
        self.owner=os.environ["DATABASE_URL"].replace('postgresql+psycopg://','postgresql://'); self.tid=str(uuid.uuid4()); self.uid=str(uuid.uuid4())
        with psycopg.connect(self.owner, autocommit=True) as c:
            c.execute("DROP ROLE IF EXISTS cs061b_auth_login")
            c.execute("CREATE ROLE cs061b_auth_login LOGIN PASSWORD 'cs061b-proof'")
            c.execute("GRANT auth_svc TO cs061b_auth_login")
            c.execute("INSERT INTO tenants(id,slug,name,status,plan,jurisdiction) VALUES(%s,%s,'proof','active','starter','NY')",(self.tid,'cs061b-proof-'+self.tid))
            from auth.passwords import Argon2idPasswordService
            c.execute("INSERT INTO tenant_users(id,tenant_id,email,password_hash,role) VALUES(%s,%s,'proof@example.test',%s,'owner')",(self.uid,self.tid,Argon2idPasswordService().hash('correct horse battery staple')))
        self.auth_url=self.owner.replace('postgresql+psycopg://postgres:postgres@', 'postgresql://cs061b_auth_login:cs061b-proof@').replace('postgresql://postgres:postgres@', 'postgresql://cs061b_auth_login:cs061b-proof@')
    def tearDown(self):
        with psycopg.connect(self.owner, autocommit=True) as c:
            c.execute("DELETE FROM tenants WHERE id=%s",(self.tid,)); c.execute("DROP ROLE IF EXISTS cs061b_auth_login")
    def repo(self):
        from auth.repository import DatabaseAuthRepository
        return DatabaseAuthRepository(psycopg.connect(self.auth_url))
    def test_narrow_functions_are_durable_and_app_roles_denied(self):
        from auth.reset import PasswordResetService
        now=datetime.now(timezone.utc); repo=self.repo(); reset=PasswordResetService('x'*32)
        secret=reset.request_reset(self.tid,self.uid); repo.issue_password_reset(self.tid,self.uid,reset.digest(secret),now + timedelta(hours=1))
        # A fresh service connection sees and atomically consumes the server-side record.
        self.assertEqual((self.tid,self.uid),self.repo().consume_password_reset(reset.digest(secret),'$argon2id$v=19$m=65536,t=3,p=4$YWJj$YWJj',now))
        self.assertIsNone(self.repo().consume_password_reset(reset.digest(secret),'x',now))
        with psycopg.connect(self.owner) as c:
            for role in ('app_rw','app_ro'):
                with c.transaction():
                    c.execute(f'SET LOCAL ROLE {role}')
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege): c.execute('SELECT * FROM auth_password_reset_tokens')
        repo.conn.close()
    def test_revocation_throttle_and_mfa_functions_are_durable(self):
        from auth.mfa import MfaSecretCipher, TOTPService, RecoveryCodeService
        now=datetime.now(timezone.utc); repo=self.repo()
        # Account failure state is visible to a completely fresh auth_svc connection.
        for _ in range(5): repo.record_login_attempt(self.tid,'proof@example.test','203.0.113.9',False,now)
        self.assertTrue(self.repo().login_locked(self.tid,'proof@example.test',now))
        self.assertFalse(self.repo().record_login_attempt(self.tid,'proof@example.test','203.0.113.9',True,now))
        self.assertFalse(self.repo().login_locked(self.tid,'proof@example.test',now))
        # Stored ciphertext is opaque to SQL callers; replay step and recovery consume are atomic.
        cipher=MfaSecretCipher.from_environment({'AUTH_MFA_ENC_KEY':'x'*44}); secret=TOTPService().generate_secret(); codes=RecoveryCodeService().generate()
        repo.store_mfa(self.tid,self.uid,cipher.encrypt(secret.encode()),-1,[RecoveryCodeService().digest(x) for x in codes])
        state=self.repo().get_mfa(self.tid,self.uid); self.assertNotIn(secret.encode(),bytes(state['ciphertext']))
        step=TOTPService().consume(secret,TOTPService().code_at(secret,now),now,last_timestep=state['last_timestep'])
        self.assertTrue(self.repo().consume_mfa_step(self.tid,self.uid,step)); self.assertFalse(self.repo().consume_mfa_step(self.tid,self.uid,step))
        digest=RecoveryCodeService().digest(codes[0]); self.assertTrue(self.repo().consume_recovery_code(self.tid,self.uid,digest)); self.assertFalse(self.repo().consume_recovery_code(self.tid,self.uid,digest))
        repo.revoke_access_token(str(uuid.uuid4()),self.tid,self.uid,now); repo.revoke_user_sessions(self.tid,self.uid,now)
        repo.conn.close()
    def test_pooled_ip_throttle_persists_across_borrowed_connections(self):
        from api.auth_gateway import PooledAuthRepository
        from psycopg_pool import ConnectionPool
        now=datetime.now(timezone.utc)
        pool=ConnectionPool(conninfo=self.auth_url, kwargs={"autocommit": True, "prepare_threshold": None}, min_size=1, open=True)
        try:
            repo=PooledAuthRepository(pool)
            for n in range(3):
                repo.record_login_attempt(self.tid, f"distributed{n}@example.test", "198.51.100.44", False, now, 3, 30)
            self.assertTrue(repo.login_locked(self.tid, "unseen@example.test", "198.51.100.44", now, 3, 30))
            self.assertFalse(repo.login_locked(self.tid, "unseen@example.test", "198.51.100.45", now, 3, 30))
        finally:
            pool.close()

if __name__ == '__main__': unittest.main()
