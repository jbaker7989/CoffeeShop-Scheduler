import { cookies } from "next/headers";
import { redirect } from "next/navigation";

type Store = {
  location_id: string;
  store_location: string;
  max_staff_on_floor: number;
  profile_type: string;
};

type StoresResponse = {
  stores: Store[];
};

const apiBaseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";

// Auth-gated, per-request, tenant-scoped data — block (render on request), never prerender/cache.
export const instant = false;

export default async function DashboardPage() {
  const cookieStore = await cookies();
  const accessToken = cookieStore.get("access_token")?.value;
  // Tenant-scoped data: forward only the access-token cookie; the gateway opens the app_rw RLS
  // read with the reconciled claim tenant_id, so a client cannot select another tenant's rows.
  const response = await fetch(`${apiBaseUrl}/stores`, {
    cache: "no-store",
    headers: accessToken ? { Cookie: `access_token=${accessToken}` } : {},
  });

  if (response.status === 401) {
    // Same-origin recovery handles one refresh + retry, then fails closed to /login (BUG-037).
    redirect("/session-refresh");
  }

  if (!response.ok) {
    throw new Error("Unable to load stores.");
  }

  const { stores } = (await response.json()) as StoresResponse;

  return (
    <section>
      <div className="mb-7 border-l-4 border-[#6e9367] pl-4">
        <p className="text-xs font-bold uppercase tracking-[0.16em] text-[#687865]">Manager workspace</p>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight sm:text-4xl">Your stores</h1>
        <p className="mt-2 text-sm text-[#657064]">Store profiles available to your team.</p>
      </div>

      {stores.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-[#cbd4c7] bg-white/60 p-8 text-sm text-[#657064]">
          No stores are available for this tenant.
        </div>
      ) : (
        <>
          <div className="grid gap-4 md:hidden">
            {stores.map((store) => (
              <article className="rounded-2xl border border-[#dbe0d6] bg-white p-5 shadow-sm" key={store.location_id}>
                <p className="text-lg font-semibold text-[#273326]">{store.store_location}</p>
                <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
                  <div>
                    <dt className="text-[#778176]">Location ID</dt>
                    <dd className="mt-0.5 break-all font-medium">{store.location_id}</dd>
                  </div>
                  <div>
                    <dt className="text-[#778176]">Floor capacity</dt>
                    <dd className="mt-0.5 font-medium">{store.max_staff_on_floor}</dd>
                  </div>
                  <div className="col-span-2">
                    <dt className="text-[#778176]">Profile</dt>
                    <dd className="mt-0.5 font-medium">{store.profile_type}</dd>
                  </div>
                </dl>
              </article>
            ))}
          </div>
          <div className="hidden overflow-hidden rounded-2xl border border-[#dbe0d6] bg-white shadow-sm md:block">
            <table className="w-full border-collapse text-left text-sm">
              <thead className="bg-[#eff4eb] text-xs font-bold uppercase tracking-[0.1em] text-[#657064]">
                <tr>
                  <th className="px-5 py-3" scope="col">Location ID</th>
                  <th className="px-5 py-3" scope="col">Store location</th>
                  <th className="px-5 py-3 text-right" scope="col">Floor capacity</th>
                  <th className="px-5 py-3" scope="col">Profile</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#e5e9e2]">
                {stores.map((store) => (
                  <tr className="text-[#354034]" key={store.location_id}>
                    <td className="px-5 py-4 font-mono text-xs">{store.location_id}</td>
                    <td className="px-5 py-4 font-semibold">{store.store_location}</td>
                    <td className="px-5 py-4 text-right">{store.max_staff_on_floor}</td>
                    <td className="px-5 py-4">{store.profile_type}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
