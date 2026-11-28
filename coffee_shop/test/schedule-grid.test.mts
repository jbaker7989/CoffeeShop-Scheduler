import assert from "node:assert/strict";
import { test } from "node:test";
import {
  applyFilters, buildWeekGrid, stationCoverage, weekNav, type Shift,
} from "../app/schedule/grid.ts";

const shift: Shift = {
  shift_id: "a", employee_id: 1, employee_name: "Demo Employee", role: "Barista",
  station_id: "bar", start_at: "2026-11-24T00:30:00Z", end_at: "2026-11-24T02:00:00Z",
  is_meal_break_covered: false, status: "published",
};

// spec: F31.ac2
test("midnight UTC shifts appear on the tenant's previous evening", () => {
  const grid = buildWeekGrid([shift], "America/New_York");
  const [row] = grid["2026-11-23"].Evening;
  assert.equal(row.startTime, "19:30");
  assert.equal(row.endTime, "21:00");
  assert.equal(row.is_meal_break_covered, false);
  assert.deepEqual(Object.keys(buildWeekGrid([shift], "Asia/Tokyo")), ["2026-11-24"]);
  assert.deepEqual(buildWeekGrid([], "America/New_York"), {});
});

// spec: F31.ac2
test("summer and winter offsets come from the tenant timezone", () => {
  const summer = { ...shift, start_at: "2026-07-01T04:30:00Z", end_at: "2026-07-01T07:00:00Z" };
  assert.equal(buildWeekGrid([summer], "America/New_York")["2026-07-01"].Overnight[0].startTime, "00:30");
});

// spec: F31.ac3
test("coverage counts expected roles and keeps uncovered and orphan stations visible", () => {
  const stations = [
    { station_id: "bar", station_type: "Espresso", staffed_by_role: "Barista" },
    { station_id: "register", station_type: "Till", staffed_by_role: "Register" },
  ];
  const coverage = stationCoverage([
    shift,
    { ...shift, shift_id: "b", role: "Register" },
    { ...shift, shift_id: "c", station_id: "orphan" },
    { ...shift, shift_id: "d", station_id: null },
  ], stations);
  assert.deepEqual(coverage.map(({ station_id, staffed, matchingRole }) => [station_id, staffed, matchingRole]), [
    ["bar", 2, 1], ["register", 0, 0], ["orphan", 1, 0], [null, 1, 0],
  ]);
});

// spec: F31.ac4
test("filter by role and status, and move one calendar week across boundaries", () => {
  const second = { ...shift, shift_id: "b", role: "Register" };
  assert.deepEqual(applyFilters([shift, second], { role: "Register", status: "published" }), [second]);
  assert.deepEqual(applyFilters([shift], { status: "draft" }), []);
  assert.equal(weekNav("2026-12-28", 1), "2027-01-04");
  assert.equal(weekNav("2026-03-09", -1), "2026-03-02");
});
