"""RS256-only access-token issuance and verification for CS-061a (F61 AC2).

Security posture (spec: F61.ac2):

* RS256 is the **only** accepted algorithm. HS*, ES*, ``none``, algorithm inference
  from the token header, JWK-URL fetching and symmetric fallback are all forbidden.
* Every access token carries a ``kid``; verification looks up *exactly* that ``kid`` in
  the environment-supplied public keyring and fails closed on an unknown/retired ``kid``.
* PEM key material is read from the environment at load time. It is never logged, echoed
  into an error message, or returned in a response — errors name the failure, not the key.
* Claims are authority-shaped: ``sub == user_id``, UUID-typed ids, ``is_saas_admin`` is
  exactly ``False`` and ``role`` is on the tenant allowlist. Canonical membership
  reconciliation (``auth.service``) is still required before the identity is trusted.
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Callable, Mapping

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from cryptography.hazmat.primitives.serialization import (
    load_pem_private_key,
    load_pem_public_key,
)

# 15-minute access tokens are fixed by the contract; this is not a tunable default.
ACCESS_TOKEN_TTL_SECONDS = 900
# The one algorithm this service will sign or verify with. Anything else fails closed.
ALLOWED_JWT_ALGORITHMS = ("RS256",)
# Tenant-user role allowlist (F61.ac1); an access token outside it is rejected.
TENANT_ROLES = ("owner", "manager", "staff", "read_only")
# Upper bound on an access token we will even attempt to parse. A well-formed RS256 token
# for these claims is well under 2 KiB; anything larger is rejected unread so a crafted
# multi-megabyte "token" cannot force expensive base64/crypto work (DoS guard).
MAX_ACCESS_TOKEN_BYTES = 8192


class TokenValidationError(Exception):
    """A token could not be trusted: wrong algorithm, unknown kid, bad signature,
    expired/not-yet-valid, missing/forbidden claim, or a malformed claim shape."""


class JWTConfigurationError(RuntimeError):
    """The RS256 keyring could not be loaded from its environment configuration.

    Raised instead of falling back to any insecure default — a missing or malformed key
    is a hard configuration failure, never a reason to degrade the algorithm or skip
    verification.
    """


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RS256KeyRing:
    """An RS256 signing key plus the set of public keys verification may consult.

    The active ``signing_kid`` mints new tokens; every public key (keyed by ``kid``) can
    still verify tokens already in flight. Rotation = publish the new public key, start
    signing with its ``kid``, then drop the old key only after the last token it signed
    can have expired.
    """

    def __init__(
        self,
        *,
        signing_kid: str,
        signing_private_pem: str,
        public_pems: Mapping[str, str],
    ) -> None:
        if not signing_kid:
            raise JWTConfigurationError("a signing kid is required")
        if not public_pems:
            raise JWTConfigurationError("at least one public verification key is required")
        if signing_kid not in public_pems:
            # The active signer must be publicly verifiable, or tokens it mints cannot be
            # validated by any peer — a silent self-inflicted outage. Fail closed at load.
            raise JWTConfigurationError("signing kid has no matching public verification key")

        self.signing_kid = signing_kid
        self._private_key = self._load_private(signing_private_pem)
        self._public_keys: dict[str, RSAPublicKey] = {
            kid: self._load_public(pem) for kid, pem in public_pems.items()
        }
        # The active private key MUST match the public key published under its kid, or every
        # token this process signs is unverifiable by any peer that trusts the JWKS — a
        # silent, total outage. Prove the pairing once, at load, and fail closed otherwise.
        if (
            self._private_key.public_key().public_numbers()
            != self._public_keys[signing_kid].public_numbers()
        ):
            raise JWTConfigurationError(
                "active signing key does not match the public key published for its kid"
            )

    @staticmethod
    def _load_private(pem: str) -> RSAPrivateKey:
        try:
            key = load_pem_private_key(pem.encode("utf-8"), password=None)
        except Exception as exc:  # malformed PEM — never echo the material
            raise JWTConfigurationError("private key PEM could not be parsed") from exc
        if not isinstance(key, RSAPrivateKey):
            # RS256 is RSA-only; an EC/Ed key here would quietly change the algorithm family.
            raise JWTConfigurationError("private key is not an RSA key (RS256 requires RSA)")
        return key

    @staticmethod
    def _load_public(pem: str) -> RSAPublicKey:
        try:
            key = load_pem_public_key(pem.encode("utf-8"))
        except Exception as exc:
            raise JWTConfigurationError("public key PEM could not be parsed") from exc
        if not isinstance(key, RSAPublicKey):
            raise JWTConfigurationError("public key is not an RSA key (RS256 requires RSA)")
        return key

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        AUTH_JWT_PRIVATE_KEY_PEM: str = "AUTH_JWT_PRIVATE_KEY_PEM",
        AUTH_JWT_PUBLIC_KEYS_PEM: str = "AUTH_JWT_PUBLIC_KEYS_PEM",
        AUTH_JWT_ACTIVE_KID: str = "AUTH_JWT_ACTIVE_KID",
    ) -> "RS256KeyRing":
        """Build the keyring from environment variables (the keyword names are the env
        keys to read, overridable for tests). ``AUTH_JWT_PUBLIC_KEYS_PEM`` is a JSON object
        mapping ``kid`` -> PEM; ``AUTH_JWT_ACTIVE_KID`` selects the signing key. A missing
        or malformed value raises :class:`JWTConfigurationError` — there is no fallback.
        """
        source = os.environ if env is None else env
        private_pem = source.get(AUTH_JWT_PRIVATE_KEY_PEM)
        public_pems_json = source.get(AUTH_JWT_PUBLIC_KEYS_PEM)
        active_kid = source.get(AUTH_JWT_ACTIVE_KID)
        if not private_pem:
            raise JWTConfigurationError(f"{AUTH_JWT_PRIVATE_KEY_PEM} is not set")
        if not public_pems_json:
            raise JWTConfigurationError(f"{AUTH_JWT_PUBLIC_KEYS_PEM} is not set")
        if not active_kid:
            raise JWTConfigurationError(f"{AUTH_JWT_ACTIVE_KID} is not set")
        try:
            public_pems = json.loads(public_pems_json)
        except json.JSONDecodeError as exc:
            raise JWTConfigurationError(f"{AUTH_JWT_PUBLIC_KEYS_PEM} is not valid JSON") from exc
        if not isinstance(public_pems, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in public_pems.items()
        ):
            raise JWTConfigurationError(
                f"{AUTH_JWT_PUBLIC_KEYS_PEM} must be a JSON object of kid -> PEM string"
            )
        return cls(
            signing_kid=active_kid,
            signing_private_pem=private_pem,
            public_pems=public_pems,
        )

    def signing_material(self) -> tuple[str, RSAPrivateKey]:
        return self.signing_kid, self._private_key

    def public_key_for(self, kid: str) -> RSAPublicKey:
        try:
            return self._public_keys[kid]
        except KeyError:
            # Unknown or retired kid fails closed; the kid is a public identifier so naming
            # it is safe, but no key material is disclosed.
            raise TokenValidationError(f"unknown or retired signing key id: {kid!r}") from None

    def public_jwks(self) -> dict:
        """The keyring's public keys as an RFC 7517 JWKS document. Only PUBLIC key material
        is ever emitted — the private signing key never appears here. Publishing every kid
        (not just the active one) lets verifiers validate tokens still in flight during a
        key rotation, which is the whole point of the kid-indexed design."""
        return {"keys": [_rsa_public_jwk(kid, key) for kid, key in self._public_keys.items()]}


class RS256TokenService:
    """Mint and validate tenant access JWTs against a :class:`RS256KeyRing`."""

    def __init__(
        self,
        key_ring: RS256KeyRing,
        *,
        issuer: str,
        audience: str,
        ttl_seconds: int = ACCESS_TOKEN_TTL_SECONDS,
        leeway_seconds: int = 0,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        if not issuer or not audience:
            raise JWTConfigurationError("issuer and audience are required")
        if leeway_seconds < 0:
            raise JWTConfigurationError("leeway must not be negative")
        # The 15-minute access lifetime is a fixed contract term (F61.ac2), not a tunable.
        # Reject any other value rather than let a caller silently mint long-lived tokens.
        if ttl_seconds != ACCESS_TOKEN_TTL_SECONDS:
            raise JWTConfigurationError(
                f"access-token TTL is fixed at {ACCESS_TOKEN_TTL_SECONDS}s"
            )
        self._key_ring = key_ring
        self._issuer = issuer
        self._audience = audience
        self._ttl_seconds = ttl_seconds
        self._leeway_seconds = leeway_seconds
        self._now = now

    def issue_access_token(
        self,
        *,
        sub: str,
        tenant_id: str,
        user_id: str,
        role: str,
        is_saas_admin: bool = False,
        jti: str | None = None,
        now: datetime | None = None,
    ) -> str:
        """Sign a 15-minute RS256 access token carrying exactly the authority claims the
        request boundary needs. ``is_saas_admin`` is always serialized as ``False`` for a
        tenant token; a SaaS-admin never enters this flow (CS-061a boundary)."""
        if role not in TENANT_ROLES:
            raise TokenValidationError(f"role {role!r} is not an allowed tenant role")
        # Validate identity claims are UUIDs *before* signing, so a malformed id can never be
        # minted into a token that would only be rejected later at verification time.
        for field_name, field_value in (("sub", sub), ("tenant_id", tenant_id), ("user_id", user_id)):
            try:
                uuid.UUID(str(field_value))
            except (TypeError, ValueError, AttributeError):
                raise TokenValidationError(f"{field_name} must be a UUID") from None
        if jti is not None:
            try:
                uuid.UUID(str(jti))
            except (TypeError, ValueError, AttributeError):
                raise TokenValidationError("jti must be a UUID") from None
        if str(sub) != str(user_id):
            raise TokenValidationError("sub must equal user_id")
        if is_saas_admin is not False:
            # Defence in depth: this service only ever mints tenant principals.
            raise TokenValidationError("is_saas_admin must be False for a tenant token")
        issued = now or self._now()
        iat = int(issued.timestamp())
        exp = iat + self._ttl_seconds
        claims = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": str(sub),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "role": role,
            "is_saas_admin": False,
            "jti": jti or str(uuid.uuid4()),
            "iat": iat,
            "nbf": iat,
            "exp": exp,
        }
        signing_kid, private_key = self._key_ring.signing_material()
        return jwt.encode(
            claims,
            private_key,
            algorithm=ALLOWED_JWT_ALGORITHMS[0],
            headers={"kid": signing_kid},
        )

    def validate_access_token(self, token: str) -> dict:
        """Verify signature, algorithm, kid, issuer, audience and temporal claims, then
        assert the claim *shape*. Returns the validated claims; raises
        :class:`TokenValidationError` on any failure. This proves token integrity only —
        canonical membership reconciliation is still the service's job."""
        # 0) Reject an oversized blob before any parsing/crypto work (DoS guard).
        if not isinstance(token, str) or len(token) > MAX_ACCESS_TOKEN_BYTES:
            raise TokenValidationError("token is missing or too large")
        # 1) Inspect the header WITHOUT trusting it, to fail closed on algorithm/kid before
        #    any signature work. This is what blocks `alg=none` and algorithm confusion.
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenValidationError("malformed token header") from exc
        if header.get("alg") not in ALLOWED_JWT_ALGORITHMS:
            raise TokenValidationError("unsupported or missing token algorithm")
        kid = header.get("kid")
        if not kid:
            raise TokenValidationError("token is missing a key id (kid)")
        public_key = self._key_ring.public_key_for(kid)

        # 2) Verify signature + registered temporal/issuer/audience claims.
        try:
            claims = jwt.decode(
                token,
                public_key,
                algorithms=list(ALLOWED_JWT_ALGORITHMS),
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway_seconds,
                options={
                    "require": ["exp", "nbf", "iat", "iss", "aud", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.PyJWTError as exc:
            # Includes expired, not-yet-valid, bad signature, wrong iss/aud, missing claim.
            raise TokenValidationError("token failed verification") from exc

        # 3) Application claim shape — the strict tenant-claim-authority invariants.
        self._validate_claim_shape(claims)
        return claims

    @staticmethod
    def _validate_claim_shape(claims: Mapping) -> None:
        jti = claims.get("jti")
        if not isinstance(jti, str) or not jti.strip():
            raise TokenValidationError("jti must be a non-empty string")
        for field in ("sub", "user_id", "tenant_id", "jti"):
            value = claims.get(field)
            try:
                uuid.UUID(str(value))
            except (TypeError, ValueError, AttributeError):
                raise TokenValidationError(f"{field} must be a UUID") from None
        if str(claims.get("sub")) != str(claims.get("user_id")):
            raise TokenValidationError("sub must equal user_id")
        # Exactly False, not merely falsy: a tenant token can never carry an admin flag.
        if claims.get("is_saas_admin") is not False:
            raise TokenValidationError("is_saas_admin must be exactly False")
        if claims.get("role") not in TENANT_ROLES:
            raise TokenValidationError("role is not an allowed tenant role")

    def public_jwks(self) -> dict:
        """Publish the verification keyring as a JWKS document (public keys only)."""
        return self._key_ring.public_jwks()

    # ----- read-only configuration (for contract fixtures that forge base claims) -----
    @property
    def issuer(self) -> str:
        return self._issuer

    @property
    def audience(self) -> str:
        return self._audience

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def sign_unchecked(self, claims: Mapping) -> str:
        """TEST/CONTRACT ONLY — sign arbitrary ``claims`` with the active key and ``kid``,
        bypassing the authority-claim guards of :meth:`issue_access_token`.

        This exists so the adversarial contract can mint a *correctly signed* token whose
        claims are deliberately malformed (``alg=none`` is still impossible — the signature
        and ``kid`` are real), proving :meth:`validate_access_token` rejects it on
        issuer/audience/time/claim-shape rather than on the signature. It is never reachable
        from a login or refresh path; production tokens come only from ``issue_access_token``.
        """
        signing_kid, private_key = self._key_ring.signing_material()
        return jwt.encode(
            dict(claims),
            private_key,
            algorithm=ALLOWED_JWT_ALGORITHMS[0],
            headers={"kid": signing_kid},
        )


def _b64url_uint(value: int) -> str:
    """Base64url-encode a non-negative integer as the minimal big-endian bytes, unpadded —
    the ``n``/``e`` encoding RFC 7518 requires for an RSA JWK."""
    length = max(1, (value.bit_length() + 7) // 8)
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode("ascii")


def _rsa_public_jwk(kid: str, public_key: RSAPublicKey) -> dict:
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _b64url_uint(numbers.n),
        "e": _b64url_uint(numbers.e),
    }
