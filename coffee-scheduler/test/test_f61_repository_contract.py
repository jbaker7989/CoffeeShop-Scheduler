"""Runtime contract for auth repository completeness (CS-061b)."""
from __future__ import annotations

import unittest
import inspect

from auth.repository import AuthRepository, DatabaseAuthRepository, InMemoryAuthRepository
from api.auth_gateway import PooledAuthRepository


_CORE_ABSTRACT_METHODS = (
    "get_tenant_user_by_email",
    "get_tenant_user_by_id",
    "record_login",
    "insert_refresh_token",
    "get_refresh_token",
    "consume_and_replace",
    "revoke_family",
    "create_invitation",
    "get_invitation",
    "accept_invitation",
    "list_invitations",
    "revoke_invitation",
    "record_audit",
)


class AuthRepositoryContractTests(unittest.TestCase):
    def test_missing_hardening_operation_rejects_adapter_instantiation(self) -> None:
        methods = {name: (lambda self, *args, **kwargs: None) for name in _CORE_ABSTRACT_METHODS}
        IncompleteRepository = type("IncompleteRepository", (AuthRepository,), methods)

        with self.assertRaises(TypeError) as raised:
            IncompleteRepository()

        self.assertIn("access_state", str(raised.exception))

    def test_fixture_mutators_are_not_part_of_production_repository_contract(self) -> None:
        self.assertFalse(hasattr(AuthRepository, "set_tenant_status"))
        self.assertFalse(hasattr(AuthRepository, "set_user_active"))

    def test_all_production_adapters_implement_the_abstract_contract(self) -> None:
        for adapter in (InMemoryAuthRepository, DatabaseAuthRepository, PooledAuthRepository):
            with self.subTest(adapter=adapter.__name__):
                self.assertFalse(inspect.isabstract(adapter))


if __name__ == "__main__":
    unittest.main()
