"""TOTP and recovery-code primitives; secrets are encrypted before persistence."""
from __future__ import annotations
import base64, hashlib, hmac, os, secrets, struct
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from datetime import datetime, timezone
TOTP_PERIOD_SECONDS=30; TOTP_WINDOW_STEPS=1
class MfaRequiredError(Exception): pass
class MfaReplayError(Exception): pass
class MfaEnrollmentError(Exception): pass
class MfaSecretCipher:
    """Versioned AES-256-GCM envelope; authentication failure never returns plaintext."""
    _VERSION = b"M1"
    def __init__(self,key): self.key=key; self._aead=AESGCM(key)
    @classmethod
    def from_environment(cls, env):
        raw=env.get('AUTH_MFA_ENC_KEY','')
        if not isinstance(raw,str) or len(raw)<32: raise ValueError('AUTH_MFA_ENC_KEY is required')
        return cls(hashlib.sha256(raw.encode()).digest())
    def encrypt(self, plaintext):
        if not isinstance(plaintext,(bytes,bytearray)): raise ValueError('plaintext required')
        nonce=os.urandom(12); return self._VERSION+nonce+self._aead.encrypt(nonce,bytes(plaintext),self._VERSION)
    def decrypt(self, ciphertext):
        if not isinstance(ciphertext,(bytes,bytearray)) or len(ciphertext)<2+12+16 or bytes(ciphertext[:2]) != self._VERSION: raise ValueError('invalid MFA ciphertext')
        nonce=bytes(ciphertext[2:14])
        try: return self._aead.decrypt(nonce,bytes(ciphertext[14:]),self._VERSION)
        except Exception as exc: raise ValueError('invalid MFA ciphertext') from exc
class TOTPService:
    def generate_secret(self): return base64.b32encode(os.urandom(20)).decode().rstrip('=')
    def _step(self, now): return int((now or datetime.now(timezone.utc)).timestamp())//TOTP_PERIOD_SECONDS
    def code_at(self, secret, now=None):
        step=self._step(now); key=base64.b32decode(secret+'='*(-len(secret)%8)); digest=hmac.new(key,struct.pack('>Q',step),hashlib.sha1).digest(); off=digest[-1]&15; return f"{(struct.unpack('>I',digest[off:off+4])[0]&0x7fffffff)%1000000:06d}"
    def verify(self, secret, code, now=None): return any(hmac.compare_digest(self.code_at(secret, datetime.fromtimestamp((self._step(now)+d)*TOTP_PERIOD_SECONDS,tz=timezone.utc)),str(code)) for d in range(-TOTP_WINDOW_STEPS,TOTP_WINDOW_STEPS+1))
    def consume(self, secret, code, now=None, last_timestep=None):
        step=self._step(now)
        for d in range(-TOTP_WINDOW_STEPS,TOTP_WINDOW_STEPS+1):
            candidate=step+d
            at=datetime.fromtimestamp(candidate*TOTP_PERIOD_SECONDS,tz=timezone.utc)
            if hmac.compare_digest(self.code_at(secret,at),str(code)):
                if last_timestep is not None and candidate<=last_timestep: raise MfaReplayError('replayed')
                return candidate
        raise MfaReplayError('invalid')
class RecoveryCodeService:
    def generate(self): return [secrets.token_urlsafe(10) for _ in range(8)]
    def digest(self, code): return hashlib.sha256(code.encode()).hexdigest()
    def consume(self, code, digests):
        digest=self.digest(code)
        if not any(hmac.compare_digest(digest,x) for x in digests): raise MfaReplayError('invalid')
        return [x for x in digests if not hmac.compare_digest(x,digest)]
    def try_consume(self, code, digests):
        try: self.consume(code,digests); return True
        except MfaReplayError: return False
