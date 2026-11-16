"use client";

import { type FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

const tenantSlug = "maven-demo";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setIsSubmitting(true);

    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tenant_slug: tenantSlug, email, password }),
      });

      if (response.status === 401) {
        setError("Invalid credentials");
        return;
      }

      if (!response.ok) {
        setError("We could not sign you in. Please try again.");
        return;
      }

      router.push("/dashboard");
      router.refresh();
    } catch {
      setError("We could not sign you in. Please try again.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-[radial-gradient(circle_at_top_left,_#dce8d5,_transparent_42%),linear-gradient(135deg,_#f7f5f0,_#edf0e8)] px-5 py-10">
      <section className="w-full max-w-md rounded-3xl border border-[#d8ded3] bg-white/90 p-7 shadow-[0_24px_60px_-32px_rgba(46,65,44,0.45)] backdrop-blur sm:p-9">
        <div className="mb-8">
          <p className="mb-3 text-xs font-bold uppercase tracking-[0.18em] text-[#687865]">Coffee Scheduler</p>
          <h1 className="text-3xl font-semibold tracking-tight text-[#1f271f]">Welcome back</h1>
          <p className="mt-2 text-sm leading-6 text-[#657064]">Sign in to manage your team&apos;s schedule.</p>
        </div>

        <form className="space-y-5" onSubmit={handleSubmit}>
          <input name="tenant_slug" type="hidden" value={tenantSlug} />
          <div>
            <label className="mb-2 block text-sm font-medium text-[#354034]" htmlFor="email">Email</label>
            <input
              autoComplete="email"
              className="block w-full rounded-xl border border-[#cbd4c7] bg-white px-3.5 py-3 text-[#1f271f] outline-none transition placeholder:text-[#8a9487] focus:border-[#55724d] focus:ring-4 focus:ring-[#dce8d5]"
              id="email"
              name="email"
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
              required
              type="email"
              value={email}
            />
          </div>
          <div>
            <label className="mb-2 block text-sm font-medium text-[#354034]" htmlFor="password">Password</label>
            <input
              autoComplete="current-password"
              className="block w-full rounded-xl border border-[#cbd4c7] bg-white px-3.5 py-3 text-[#1f271f] outline-none transition placeholder:text-[#8a9487] focus:border-[#55724d] focus:ring-4 focus:ring-[#dce8d5]"
              id="password"
              name="password"
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
          </div>
          {error ? (
            <p aria-live="polite" className="rounded-lg bg-[#fff0ef] px-3 py-2 text-sm text-[#a73831]" role="alert">{error}</p>
          ) : null}
          <button
            className="w-full rounded-xl bg-[#315c37] px-4 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-[#274b2d] focus:outline-none focus:ring-4 focus:ring-[#b8d2b4] disabled:cursor-not-allowed disabled:opacity-60"
            disabled={isSubmitting}
            type="submit"
          >
            {isSubmitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}
