export type Shift = {
  shift_id: string;
  employee_id: number;
  employee_name: string | null;
  role: string | null;
  station_id: string | null;
  start_at: string;
  end_at: string;
  is_meal_break_covered: boolean | null;
  status: string;
};

export type Station = {
  station_id: string;
  station_type: string | null;
  staffed_by_role: string | null;
};

export const dayparts = ["Overnight", "Morning", "Afternoon", "Evening"] as const;
type Daypart = (typeof dayparts)[number];
type GridShift = Shift & { startTime: string; endTime: string; endDay: string };
type DayGrid = Partial<Record<Daypart, GridShift[]>>;

export function buildWeekGrid(shifts: Shift[], timezone: string): Record<string, DayGrid> {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });
  function localTime(timestamp: string) {
    const parts = Object.fromEntries(
      formatter.formatToParts(new Date(timestamp)).map(({ type, value }) => [type, value]),
    );
    return {
      day: `${parts.year}-${parts.month}-${parts.day}`,
      time: `${parts.hour}:${parts.minute}`,
      hour: Number(parts.hour),
    };
  }

  const grid: Record<string, DayGrid> = {};
  const sorted = [...shifts].sort((a, b) => Date.parse(a.start_at) - Date.parse(b.start_at));
  for (const shift of sorted) {
    const start = localTime(shift.start_at);
    const end = localTime(shift.end_at);
    const daypart = dayparts[Math.floor(start.hour / 6)];
    const day = (grid[start.day] ??= {});
    (day[daypart] ??= []).push({ ...shift, startTime: start.time, endTime: end.time, endDay: end.day });
  }
  return grid;
}

export function stationCoverage(shifts: Shift[], stations: Station[]) {
  const coverage = new Map<string | null, {
    station_id: string | null;
    station_type: string | null;
    staffed_by_role: string | null;
    staffed: number;
    matchingRole: number;
  }>();
  for (const station of stations) {
    coverage.set(station.station_id, { ...station, staffed: 0, matchingRole: 0 });
  }
  for (const shift of shifts) {
    let entry = coverage.get(shift.station_id);
    if (!entry) {
      entry = {
        station_id: shift.station_id, station_type: null, staffed_by_role: null, staffed: 0, matchingRole: 0,
      };
      coverage.set(shift.station_id, entry);
    }
    entry.staffed++;
    if (entry.staffed_by_role !== null && entry.staffed_by_role === shift.role) {
      entry.matchingRole++;
    }
  }
  return [...coverage.values()];
}

export function applyFilters(shifts: Shift[], { role, status }: { role?: string; status?: string }) {
  return shifts.filter((shift) => (!role || shift.role === role) && (!status || shift.status === status));
}

function offsetDate(day: string, days: number) {
  // ISO calendar dates have no wall-clock time. UTC arithmetic keeps them independent of DST.
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function weekNav(weekStart: string, direction: -1 | 1) {
  return offsetDate(weekStart, direction * 7);
}

export function weekDays(weekStart: string) {
  return Array.from({ length: 7 }, (_, day) => offsetDate(weekStart, day));
}
