import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { LogoutButton } from "./logout-button";

type Me = {
  tenant_id: string;
  user_id: string;
  role: string;
};

const apiBaseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";

// Auth-gated shell that reads the CS-061a access-token cookie per request — block, render dynamically.
export const instant = false;

export default async function ManagerLayout({ children }: { children: ReactNode }) {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get("access_token")?.value;
  // Forward ONLY the access-token cookie as the single credential the gateway reconciles; the
  // tenant is derived from the signed claims, never from anything the client could set.
  const response = await fetch(`${apiBaseUrl}/me`, {
    cache: "no-store",
    headers: accessToken ? { Cookie: `access_token=${accessToken}` } : {},
  });

  if (response.status === 401) {
    // Access credential expired/absent: hand off to the same-origin recovery step, which attempts
    // one refresh and then either retries this page or fails closed to /login (BUG-037).
    redirect("/session-refresh");
  }

  if (!response.ok) {
    throw new Error("Unable to load the current session.");
  }

  const me = (await response.json()) as Me;

  return (
    <div className="min-h-screen bg-[#f7f5f0] text-[#1f271f]">
      <header className="border-b border-[#dbe0d6] bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3 sm:px-6 lg:px-8">
          <a className="shrink-0 text-base font-bold tracking-tight text-[#284b2e]" href="/dashboard">
            Coffee Scheduler
          </a>
          <nav aria-label="Manager navigation" className="order-3 flex w-full gap-1 sm:order-none sm:w-auto">
            {["Schedule", "Rules", "Forecast"].map((label) => (
              <span className="rounded-md px-2.5 py-1.5 text-sm text-[#778176]" key={label}>{label}</span>
            ))}
          </nav>
          <div className="ml-auto flex min-w-0 items-center gap-3">
            <div className="min-w-0 text-right text-xs leading-4">
              <p className="capitalize truncate font-semibold text-[#354034]">{me.role}</p>
              <p className="truncate font-mono text-[#778176]" title={me.tenant_id}>{me.tenant_id.slice(0, 8)}</p>
            </div>
            <LogoutButton />
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">{children}</main>
    </div>
  );
}
