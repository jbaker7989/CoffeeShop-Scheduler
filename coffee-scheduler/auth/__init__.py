"""CS-061a / F61 tenant authentication package.

This package is split across two implementation slices (see ``docs/sdd-cs061a.md``):

- **Persistence + credentials** (this slice): ``auth.passwords`` (Argon2id-only hashing)
  and ``auth.repository`` (identity + refresh-family persistence, with an in-memory
  contract fixture and a psycopg3-backed store).
- **Tokens, service, and routes** (sibling slice): ``auth.jwt``, ``auth.tokens``,
  ``auth.service``, and ``auth.api`` build on the modules here.

Nothing in this package logs or persists raw passwords or raw refresh/access tokens.
"""
