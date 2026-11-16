// Unit tests for the BUG-037 session-recovery policy. Run: `node --test test/`.
// Pure logic, no DOM — proves expired-access -> one refresh -> single retry, and fail-closed.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  decideSessionRecovery,
  LOGIN_TARGET,
  RETRY_TARGET,
} from "../app/session-recovery.ts";

test("successful refresh (200) on a first attempt retries the page exactly once", () => {
  assert.equal(decideSessionRecovery(200, false), "retry");
});

test("a replayed or expired refresh (401) fails closed to login", () => {
  assert.equal(decideSessionRecovery(401, false), "login");
});

test("a network error (status 0) fails closed to login", () => {
  assert.equal(decideSessionRecovery(0, false), "login");
});

test("any non-200 refresh status fails closed to login", () => {
  for (const status of [400, 403, 404, 429, 500, 503]) {
    assert.equal(decideSessionRecovery(status, false), "login", `status ${status}`);
  }
});

test("a second recovery in the same cycle never refreshes again (bounded to one retry)", () => {
  // Even a 200 is ignored once a recovery was already attempted: no unbounded refresh loop.
  assert.equal(decideSessionRecovery(200, true), "login");
  assert.equal(decideSessionRecovery(401, true), "login");
});

test("redirect targets are fixed routes, never a user-controlled return URL", () => {
  assert.equal(RETRY_TARGET, "/dashboard");
  assert.equal(LOGIN_TARGET, "/login");
});
