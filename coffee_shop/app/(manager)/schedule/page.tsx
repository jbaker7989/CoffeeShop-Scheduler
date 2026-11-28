import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import {
  applyFilters, buildWeekGrid, dayparts, stationCoverage, weekDays, weekNav, type Shift, type Station,
} from "../../schedule/grid";

type Store = { location_id: number; store_location: string };
type Roster = {
  schedule_id: string | null;
  week_start: string;
  timezone: string;
  shifts: Shift[];
  stations: Station[];
};
type Rule = { rule_id: string; result: string; reason: string | null };
type SearchParams = Record<string, string | string[] | undefined>;

const apiBaseUrl = process.env.API_BASE_URL ?? "http://localhost:8000";
const panel = "rounded-2xl border border-[#dbe0d6] bg-white p-5 shadow-sm";
const control = "rounded-md border border-[#cbd4c7] bg-white px-3 py-2 text-sm";
const heading = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" });

// Time, identity, and published roster are resolved per request, as on the dashboard.
export const instant = false;

export default async function SchedulePage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const query = await searchParams;
  const cookieStore = await cookies();
  const accessToken = cookieStore.get("access_token")?.value;

  async function read<T>(path: string): Promise<T> {
    const response = await fetch(`${apiBaseUrl}${path}`, {
      cache: "no-store",
      headers: accessToken ? { Cookie: `access_token=${accessToken}` } : {},
    });
    if (response.status === 401) redirect("/session-refresh");
    if (!response.ok) throw new Error("Unable to load the schedule.");
    return response.json() as Promise<T>;
  }

  const { stores } = await read<{ stores: Store[] }>("/stores");
  stores.sort((a, b) => a.location_id - b.location_id);
  const field = (name: string) => typeof query[name] === "string" ? query[name] : undefined;
  const requestedStore = field("location_id");
  const location = requestedStore && /^-?\d{1,10}$/.test(requestedStore)
    && Number(requestedStore) >= -(2 ** 31) && Number(requestedStore) < 2 ** 31
    ? Number(requestedStore) : stores[0]?.location_id;
  const requestedWeek = field("week_start");
  const date = requestedWeek && /^\d{4}-\d{2}-\d{2}$/.test(requestedWeek)
    ? new Date(`${requestedWeek}T00:00:00Z`) : null;
  const week = date && Number.isFinite(+date) && date.toISOString().slice(0, 10) === requestedWeek
    && date.getUTCFullYear() >= 2 && date.getUTCFullYear() <= 9998
    ? requestedWeek : undefined;
  const role = field("role") ?? "";
  const status = field("status") ?? "";
  const rosterQuery = new URLSearchParams();
  if (location !== undefined) rosterQuery.set("location_id", String(location));
  if (week) rosterQuery.set("week_start", week);
  const roster = location === undefined ? null : await read<Roster>(`/schedule?${rosterQuery}`);

  const shifts = roster?.shifts ?? [];
  const filtered = applyFilters(shifts, { role, status });
  const grid = roster ? buildWeekGrid(filtered, roster.timezone) : {};
  const coverage = stationCoverage(filtered, roster?.stations ?? []);
  const stationNames = new Map(roster?.stations.map((station) => [station.station_id, station.station_type]));
  const roles = [...new Set(shifts.flatMap((shift) => shift.role ? [shift.role] : []))].sort();
  if (role && !roles.includes(role)) roles.push(role);
  const selected = filtered.find((shift) => shift.shift_id === field("shift"));
  const rules = selected
    ? (await read<{ rules: Rule[] }>(`/schedule/shift/${encodeURIComponent(selected.shift_id)}/rules`)).rules
    : [];
  function scheduleLink(weekStart: string, shiftId?: string) {
    const params = new URLSearchParams(rosterQuery);
    params.set("week_start", weekStart);
    if (role) params.set("role", role);
    if (status) params.set("status", status);
    if (shiftId) params.set("shift", shiftId);
    return `/schedule?${params}${shiftId ? "#shift-rules" : ""}`;
  }

  return (
    <section className="space-y-6">
      <header className="border-l-4 border-[#6e9367] pl-4">
        <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Weekly schedule</h1>
        {roster && <p className="mt-2 text-sm text-[#657064]">{roster.timezone}</p>}
      </header>

      {roster && (
        <div className={`${panel} flex flex-wrap items-end justify-between gap-4`}>
          <form action="/schedule" method="get" className="flex flex-wrap items-end gap-3">
            <input type="hidden" name="week_start" value={roster.week_start} />
            <label className="grid gap-1 text-sm font-medium">
              Store
              <select className={control} name="location_id" defaultValue={location}>
                {!stores.some((store) => store.location_id === location) && <option value={location}>Unknown store</option>}
                {stores.map((store) => <option key={store.location_id} value={store.location_id}>{store.store_location}</option>)}
              </select>
            </label>
            <label className="grid gap-1 text-sm font-medium">
              Role
              <select className={control} name="role" defaultValue={role}>
                <option value="">All roles</option>
                {roles.map((value) => <option key={value}>{value}</option>)}
              </select>
            </label>
            <label className="grid gap-1 text-sm font-medium">
              Status
              <select className={control} name="status" defaultValue={status}>
                <option value="">All visible statuses</option>
                <option value="published">Published</option>
              </select>
            </label>
            <button className="rounded-md bg-[#284b2e] px-4 py-2 text-sm font-semibold text-white" type="submit">Apply filters</button>
          </form>
          <nav aria-label="Schedule weeks" className="flex flex-wrap items-center gap-2 text-sm">
            <a className={control} href={scheduleLink(weekNav(roster.week_start, -1))}>Previous week</a>
            <time dateTime={roster.week_start} className="px-1 font-semibold">{roster.week_start}</time>
            <a className={control} href={scheduleLink(weekNav(roster.week_start, 1))}>Next week</a>
          </nav>
        </div>
      )}

      {!roster?.schedule_id ? (
        <p className="rounded-2xl border border-dashed border-[#cbd4c7] bg-white/60 p-8 text-sm text-[#657064]">
          No published schedule for this week.
        </p>
      ) : (
        <>
          {selected && (
            <aside id="shift-rules" className={panel}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="font-semibold">Rules for {selected.employee_name ?? `employee ${selected.employee_id}`}</h2>
                <a className="text-sm underline" href={scheduleLink(roster.week_start)}>Close rules</a>
              </div>
              {rules.length ? (
                <ul className="mt-3 space-y-2 text-sm">
                  {rules.map((rule, index) => (
                    <li key={`${rule.rule_id}-${index}`}>
                      <span className="font-semibold">{rule.rule_id} · {rule.result}</span>
                      {rule.reason && <p>{rule.reason}</p>}
                    </li>
                  ))}
                </ul>
              ) : <p className="mt-3 text-sm text-[#657064]">No rule evaluations recorded.</p>}
            </aside>
          )}

          {filtered.length === 0 && <p className="text-sm text-[#657064]">No shifts match this view.</p>}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
            {weekDays(roster.week_start).map((day) => (
              <article className={panel} key={day}>
                <h2 className="font-semibold"><time dateTime={day}>{heading.format(new Date(`${day}T00:00:00Z`))}</time></h2>
                {!grid[day] && <p className="mt-2 text-sm text-[#657064]">No shifts</p>}
                {dayparts.map((daypart) => {
                  const rows = grid[day]?.[daypart];
                  return rows && (
                    <div key={daypart} className="mt-4">
                      <h3 className="text-xs font-semibold uppercase tracking-wide text-[#657064]">{daypart}</h3>
                      <ul className="mt-1 space-y-2">
                        {rows.map((shift) => (
                          <li className="rounded-lg border border-[#e5e9e2] p-2 text-sm" key={`${shift.shift_id}-${shift.employee_id}`}>
                            <a className="font-semibold text-[#284b2e] underline" href={scheduleLink(roster.week_start, shift.shift_id)}>
                              {shift.employee_name ?? `Employee ${shift.employee_id}`}
                            </a>
                            <p>{shift.startTime}–{shift.endTime}{shift.endDay !== day && ` (${shift.endDay})`}</p>
                            <p>{shift.role ?? "Role unrecorded"} · {stationNames.get(shift.station_id ?? "") ?? shift.station_id ?? "Unassigned station"}</p>
                            <p className="text-[#657064]">
                              Meal break {shift.is_meal_break_covered === null ? "unrecorded"
                                : shift.is_meal_break_covered ? "covered" : "uncovered"}
                            </p>
                          </li>
                        ))}
                      </ul>
                    </div>
                  );
                })}
              </article>
            ))}
          </div>

          {coverage.length > 0 && (
            <article className={panel}>
              <h2 className="mb-3 font-semibold">Station coverage</h2>
              <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {coverage.map((station) => (
                  <li className="rounded-lg border border-[#e5e9e2] p-3 text-sm" key={station.station_id ?? "unassigned"}>
                    <p className="font-semibold">{station.station_type ?? station.station_id ?? "Unassigned shifts"}</p>
                    <p>{station.staffed ? `${station.staffed} assignments` : "Uncovered"}</p>
                    {station.staffed_by_role && <p>{station.matchingRole} staffed as {station.staffed_by_role}</p>}
                  </li>
                ))}
              </ul>
            </article>
          )}
        </>
      )}
    </section>
  );
}
