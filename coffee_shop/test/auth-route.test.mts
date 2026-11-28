import assert from "node:assert/strict";
import { test } from "node:test";
import { loginRedirectForPath } from "../app/auth-route.ts";

// spec: F30 — BUG-032 protects current and future manager pages.
test("manager routes send requests without an access cookie to login", () => {
  for (const path of ["/dashboard", "/schedule", "/schedule/week", "/rules", "/apiary"]) {
    assert.equal(loginRedirectForPath(path, false), "/login", path);
    assert.equal(loginRedirectForPath(path, true), null, path);
  }
  assert.equal(loginRedirectForPath("/t/acme/dashboard", false), "/t/acme/login");
  assert.equal(loginRedirectForPath("/t/acme/schedule", false), "/t/acme/login");
});

test("public pages, gateway routes, and assets pass without an access cookie", () => {
  for (const path of [
    "/", "/login", "/session-refresh", "/api", "/api/stores", "/auth/browser/login",
    "/_next/static/chunks/app.js", "/_next/image", "/favicon.ico", "/window.svg",
    "/t/acme/login", "/t/acme/session-refresh", "/t/acme/api/stores", "/t/acme/auth/browser/login",
  ]) {
    assert.equal(loginRedirectForPath(path, false), null, path);
  }
});
