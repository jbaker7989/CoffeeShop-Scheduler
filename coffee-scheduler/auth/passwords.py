"""Argon2id-only password hashing/verification (CS-061a / F61 AC1).

The service accepts and produces **only** Argon2id (`$argon2id$`) encoded hashes. A
stored credential in any other encoding (a legacy `$argon2i$`, bcrypt, a plaintext
value, anything) is rejected as tampering/misconfiguration rather than silently
treated as a non-match — fail closed, per the repo's D2 "no silent-default" rule.

Raw passwords are never stored or logged: this module only ever returns an encoded
hash, and exception messages never include the candidate password.
"""
from __future__ import annotations

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Every hash this service emits, and every stored hash it will verify against, must
# begin with this prefix. Argon2id is the only memory-hard variant resistant to both
# side-channel and GPU attacks, so it is the sole accepted encoding.
ARGON2ID_PREFIX = "$argon2id$"


class InvalidPasswordHash(ValueError):
    """Raised when a stored credential is not a well-formed Argon2id encoded hash."""


class Argon2idPasswordService:
    """Hash and verify passwords using Argon2id, and nothing else.

    A single ``PasswordHasher`` (pinned to ``Type.ID``) is reused; its parameters are
    argon2-cffi's vetted defaults. Callers that need custom cost parameters may inject
    their own ``Type.ID`` hasher.
    """

    def __init__(self, hasher: PasswordHasher | None = None) -> None:
        self._hasher = hasher or PasswordHasher(type=Type.ID)

    def hash(self, password: str) -> str:
        """Return an Argon2id encoded hash of ``password``."""
        encoded = self._hasher.hash(password)
        # Defense in depth: never hand back a non-Argon2id encoding, even if an
        # injected hasher were misconfigured to a different variant.
        if not encoded.startswith(ARGON2ID_PREFIX):
            raise InvalidPasswordHash("password hasher did not produce an Argon2id hash")
        return encoded

    def verify(self, password: str, encoded: str) -> bool:
        """Return True iff ``password`` matches the Argon2id ``encoded`` hash.

        Returns False for a well-formed Argon2id hash that does not match. Raises
        ``InvalidPasswordHash`` if ``encoded`` is not an Argon2id hash at all (wrong
        algorithm or malformed), so a bad stored value can never be mistaken for a
        benign authentication failure.
        """
        if not isinstance(encoded, str) or not encoded.startswith(ARGON2ID_PREFIX):
            raise InvalidPasswordHash("stored credential is not an Argon2id hash")
        try:
            return self._hasher.verify(encoded, password)
        except VerifyMismatchError:
            return False
        except (InvalidHashError, VerificationError) as exc:
            # Starts with the prefix but is otherwise corrupt: still fail closed.
            raise InvalidPasswordHash("stored credential is not a valid Argon2id hash") from exc

    def needs_rehash(self, encoded: str) -> bool:
        """True if ``encoded`` should be re-hashed to the current cost parameters."""
        if not isinstance(encoded, str) or not encoded.startswith(ARGON2ID_PREFIX):
            raise InvalidPasswordHash("stored credential is not an Argon2id hash")
        return self._hasher.check_needs_rehash(encoded)
