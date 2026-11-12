import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const dataDir = process.env.COFFEESHOP_DATA_DIR ?? new URL(".", import.meta.url).pathname;
const workbookPath = new URL("coffee_shop_labor_rules.xlsx", `file://${dataDir.endsWith("/") ? dataDir : `${dataDir}/`}`).pathname;
const asOf = new Date("2026-11-12T00:00:00Z");
const DATE = (value) => new Date(`${value}T00:00:00Z`);
const iso = (date) => date.toISOString().slice(0, 10);
const sourceBase = "https://www.schools.nyc.gov/calendar";

function range(start, end) {
  const dates = [];
  for (let cursor = DATE(start); cursor <= DATE(end); cursor.setUTCDate(cursor.getUTCDate() + 1)) {
    dates.push(iso(cursor));
  }
  return dates;
}

function addRange(target, start, end, note) {
  for (const date of range(start, end)) target.set(date, note);
}

function nthWeekday(year, month, weekday, occurrence) {
  const first = new Date(Date.UTC(year, month - 1, 1));
  const offset = (weekday - first.getUTCDay() + 7) % 7;
  return new Date(Date.UTC(year, month - 1, 1 + offset + 7 * (occurrence - 1)));
}

function lastWeekday(year, month, weekday) {
  const last = new Date(Date.UTC(year, month, 0));
  const offset = (last.getUTCDay() - weekday + 7) % 7;
  last.setUTCDate(last.getUTCDate() - offset);
  return last;
}

function observed(date) {
  const day = date.getUTCDay();
  if (day === 6) date.setUTCDate(date.getUTCDate() - 1);
  if (day === 0) date.setUTCDate(date.getUTCDate() + 1);
  return date;
}

function federalHolidays(year) {
  return [
    ["New Year's Day", observed(new Date(Date.UTC(year, 0, 1)))],
    ["Martin Luther King Jr. Day", nthWeekday(year, 1, 1, 3)],
    ["Washington's Birthday", nthWeekday(year, 2, 1, 3)],
    ["Memorial Day", lastWeekday(year, 5, 1)],
    ["Juneteenth National Independence Day", observed(new Date(Date.UTC(year, 5, 19)))],
    ["Independence Day", observed(new Date(Date.UTC(year, 6, 4)))],
    ["Labor Day", nthWeekday(year, 9, 1, 1)],
    ["Columbus Day", nthWeekday(year, 10, 1, 2)],
    ["Veterans Day", observed(new Date(Date.UTC(year, 10, 11)))],
    ["Thanksgiving Day", nthWeekday(year, 11, 4, 4)],
    ["Christmas Day", observed(new Date(Date.UTC(year, 11, 25)))],
  ];
}

function nyStateHolidays(year) {
  return [
    ...federalHolidays(year),
    ["Lincoln's Birthday", new Date(Date.UTC(year, 1, 12))],
    // NY Election Day = first Tuesday AFTER the first Monday of November (i.e. first Monday + 1 day),
    // not simply the first Tuesday (which is wrong whenever Nov 1 falls on a Tuesday).
    ["Election Day", new Date(nthWeekday(year, 11, 1, 1).getTime() + 86400000)],
  ];
}

const officialTerms = [
  {
    id: "NYC-DOE-2022-23",
    start: "2022-09-08", end: "2023-06-27",
    url: "https://www.schools.nyc.gov/calendar/2022-2023-school-year-calendar",
    closures: [["2023-01-02", "2023-01-02", "New Year's Day observed"], ["2023-01-16", "2023-01-16", "Martin Luther King Jr. Day"], ["2023-02-20", "2023-02-24", "Midwinter Recess"], ["2023-04-06", "2023-04-14", "Spring Recess"], ["2023-04-21", "2023-04-21", "Eid al-Fitr"], ["2023-05-29", "2023-05-29", "Memorial Day"], ["2023-06-19", "2023-06-19", "Juneteenth"]],
  },
  {
    id: "NYC-DOE-2023-24",
    start: "2023-09-07", end: "2024-06-26",
    url: "https://www.schools.nyc.gov/about-us/messages-for-families/past-messages-2023",
    closures: [["2023-09-15", "2023-09-15", "Rosh Hashanah"], ["2023-09-25", "2023-09-25", "Yom Kippur"], ["2023-10-09", "2023-10-09", "Italian Heritage/Indigenous Peoples' Day"], ["2023-11-07", "2023-11-07", "Election Day"], ["2023-11-23", "2023-11-24", "Thanksgiving Recess"], ["2023-12-25", "2024-01-01", "Winter Recess"], ["2024-01-15", "2024-01-15", "Martin Luther King Jr. Day"], ["2024-02-19", "2024-02-23", "Midwinter Recess"], ["2024-03-29", "2024-03-29", "Good Friday"], ["2024-04-10", "2024-04-10", "Eid al-Fitr"], ["2024-04-22", "2024-04-30", "Spring Recess"], ["2024-05-27", "2024-05-27", "Memorial Day"], ["2024-06-17", "2024-06-17", "Eid al-Adha"], ["2024-06-19", "2024-06-19", "Juneteenth"]],
  },
  {
    id: "NYC-DOE-2024-25",
    start: "2024-09-05", end: "2025-06-26",
    url: "https://www.schools.nyc.gov/calendar/2024-2025-school-year-calendar",
    closures: [["2024-10-03", "2024-10-04", "Rosh Hashanah"], ["2024-10-14", "2024-10-14", "Italian Heritage/Indigenous Peoples' Day"], ["2024-11-01", "2024-11-01", "Diwali"], ["2024-11-05", "2024-11-05", "Election Day"], ["2024-11-11", "2024-11-11", "Veterans Day"], ["2024-11-28", "2024-11-29", "Thanksgiving Recess"], ["2024-12-24", "2025-01-01", "Winter Recess"], ["2025-01-20", "2025-01-20", "Martin Luther King Jr. Day"], ["2025-01-29", "2025-01-29", "Lunar New Year"], ["2025-02-17", "2025-02-21", "Midwinter Recess"], ["2025-03-31", "2025-03-31", "Eid al-Fitr"], ["2025-04-14", "2025-04-18", "Spring Recess"], ["2025-05-26", "2025-05-26", "Memorial Day"], ["2025-06-06", "2025-06-06", "Eid al-Adha"], ["2025-06-19", "2025-06-19", "Juneteenth"]],
  },
  {
    id: "NYC-DOE-2025-26",
    start: "2025-09-04", end: "2026-06-26",
    url: "https://www.schools.nyc.gov/calendar/2025-2026-school-year-calendar",
    closures: [["2025-09-23", "2025-09-24", "Rosh Hashanah"], ["2025-10-02", "2025-10-02", "Yom Kippur"], ["2025-10-13", "2025-10-13", "Italian Heritage/Indigenous Peoples' Day"], ["2025-10-20", "2025-10-20", "Diwali"], ["2025-11-04", "2025-11-04", "Election Day"], ["2025-11-11", "2025-11-11", "Veterans Day"], ["2025-11-27", "2025-11-28", "Thanksgiving Recess"], ["2025-12-24", "2026-01-02", "Winter Recess"], ["2026-01-19", "2026-01-19", "Martin Luther King Jr. Day"], ["2026-02-16", "2026-02-20", "Midwinter Recess"], ["2026-03-20", "2026-03-20", "Eid al-Fitr"], ["2026-04-02", "2026-04-10", "Spring Recess"], ["2026-05-25", "2026-05-25", "Memorial Day"], ["2026-05-27", "2026-05-27", "Eid al-Adha"], ["2026-06-04", "2026-06-05", "Staff development / clerical days"], ["2026-06-19", "2026-06-19", "Juneteenth"]],
  },
  {
    id: "NYC-DOE-2026-27",
    start: "2026-09-10", end: "2027-06-28",
    url: "https://www.schools.nyc.gov/calendar/2026-2027-school-year-calendar",
    closures: [["2026-09-21", "2026-09-21", "Yom Kippur"], ["2026-10-12", "2026-10-12", "Italian Heritage/Indigenous Peoples' Day"], ["2026-11-11", "2026-11-11", "Veterans Day"], ["2026-11-26", "2026-11-27", "Thanksgiving Recess"], ["2026-12-24", "2027-01-01", "Winter Recess"], ["2027-01-18", "2027-01-18", "Martin Luther King Jr. Day"], ["2027-02-15", "2027-02-19", "Midwinter Recess"], ["2027-03-09", "2027-03-09", "Eid al-Fitr"], ["2027-03-26", "2027-03-26", "Good Friday"], ["2027-04-22", "2027-04-30", "Spring Recess"], ["2027-05-17", "2027-05-17", "Eid al-Adha"], ["2027-05-31", "2027-05-31", "Memorial Day"], ["2027-06-08", "2027-06-08", "Clerical Day"], ["2027-06-10", "2027-06-10", "Anniversary Day"]],
  },
];

function dateInRange(value, start, end) {
  return value >= start && value <= end;
}

function buildSchoolRows() {
  const holidayDates = new Set();
  for (let year = 2023; year <= 2028; year += 1) {
    for (const [, date] of [...federalHolidays(year), ...nyStateHolidays(year)]) holidayDates.add(iso(date));
  }
  const rowsByDate = new Map();
  for (const date of range("2023-01-01", "2028-12-31")) {
    const weekday = DATE(date).getUTCDay();
    rowsByDate.set(date, { id: "NYC-DOE-BASELINE", inSession: false, source: "NYCPS published calendars", url: sourceBase, assumed: false, notes: weekday === 0 || weekday === 6 ? "Weekend" : "Outside published instructional term" });
  }
  for (const term of officialTerms) {
    const closures = new Map();
    for (const [start, end, note] of term.closures) addRange(closures, start, end, note);
    for (const date of range(term.start, term.end)) {
      if (date < "2023-01-01" || date > "2028-12-31") continue;
      const weekday = DATE(date).getUTCDay();
      rowsByDate.set(date, { id: term.id, inSession: weekday !== 0 && weekday !== 6 && !closures.has(date), source: term.id, url: term.url, assumed: false, notes: closures.get(date) ?? (weekday === 0 || weekday === 6 ? "Weekend" : "Instructional day or attendance day") });
    }
  }
  for (const year of [2027, 2028]) {
    const laborDay = iso(nthWeekday(year, 9, 1, 1));
    const start = iso(new Date(Date.UTC(year, 8, DATE(laborDay).getUTCDate() + 1)));
    const end = iso(lastWeekday(year + 1, 6, 5));
    for (const date of range(start, end)) {
      if (date < "2027-09-01" || date > "2028-12-31") continue;
      const weekday = DATE(date).getUTCDay();
      rowsByDate.set(date, { id: "NYC-DOE-2027-28-PLANNING", inSession: weekday !== 0 && weekday !== 6 && !holidayDates.has(date), source: "Planning assumption — NYCPS 2027–28 unpublished", url: sourceBase, assumed: true, notes: "Replace with the official NYCPS calendar when published" });
    }
  }
  return [...rowsByDate.entries()].sort(([left], [right]) => left.localeCompare(right)).map(([date, row]) => {
    const next = rowsByDate.get(iso(new Date(DATE(date).getTime() + 86400000)));
    return [row.id, DATE(date), row.inSession, Boolean(next?.inSession), row.source, row.url, asOf, null, row.assumed, row.notes];
  });
}

function buildHolidayRows() {
  const rows = [];
  for (let year = 2023; year <= 2028; year += 1) {
    for (const [name, date] of federalHolidays(year)) rows.push([`FED-${year}-${iso(date)}`, date, name, "US-FED", "OPM federal holiday schedule", "https://www.opm.gov/policy-data-oversight/pay-leave/federal-holidays/", asOf, null, false, "Observed date"]);
    for (const [name, date] of nyStateHolidays(year)) rows.push([`NYS-${year}-${iso(date)}-${name.replaceAll(/[^A-Za-z]/g, "").slice(0, 8)}`, date, name, "NY-STATE", "NYS legal holiday calendar", "https://www.cs.ny.gov/attendance_leave/index.cfm", asOf, null, year === 2028, year === 2028 ? "Planning assumption — official NYS 2028 schedule unpublished" : "Legal holiday / state observance"]);
  }
  return rows.sort((left, right) => left[1] - right[1] || left[3].localeCompare(right[3]));
}

const schoolHeaders = [["calendar_id", "date", "in_session", "day_before_school_day", "calendar_source", "source_url", "effective_from", "effective_to", "is_assumption", "notes"]];
const holidayHeaders = [["holiday_id", "date", "holiday", "jurisdiction", "calendar_source", "source_url", "effective_from", "effective_to", "is_assumption", "notes"]];

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
const school = workbook.worksheets.getItem("School_Calendar");
const holidays = workbook.worksheets.getItem("Holiday_Calendar");
const readme = workbook.worksheets.getItem("README");
const schoolRows = buildSchoolRows();
const holidayRows = buildHolidayRows();

school.getRange("A1:J3000").clear({ applyTo: "contents" });
school.getRange(`A1:J${schoolRows.length + 1}`).values = [...schoolHeaders, ...schoolRows];
holidays.getRange("A1:J3000").clear({ applyTo: "contents" });
holidays.getRange(`A1:J${holidayRows.length + 1}`).values = [...holidayHeaders, ...holidayRows];

for (const [sheet, range] of [[school, `A1:J${schoolRows.length + 1}`], [holidays, `A1:J${holidayRows.length + 1}`]]) {
  sheet.getRange(range).format.font = { name: "Arial", size: 10 };
  sheet.getRange(range).format.verticalAlignment = "center";
  sheet.getRange("A1:J1").format.fill = "#6C4C3D";
  sheet.getRange("A1:J1").format.font = { name: "Arial", size: 10, bold: true, color: "#FFFFFF" };
  sheet.getRange("A1:J1").format.horizontalAlignment = "center";
  sheet.getRange("A1:J1").format.rowHeight = 22;
  sheet.getRange("B2:B3000").setNumberFormat("yyyy-mm-dd");
  sheet.getRange("G2:H3000").setNumberFormat("yyyy-mm-dd");
  sheet.freezePanes.freezeRows(1);
  sheet.getRange("A:A").format.columnWidth = 25;
  sheet.getRange("B:B").format.columnWidth = 13;
  sheet.getRange("C:C").format.columnWidth = 17;
  sheet.getRange("D:D").format.columnWidth = 26;
  sheet.getRange("E:E").format.columnWidth = 36;
  sheet.getRange("F:F").format.columnWidth = 42;
  sheet.getRange("G:H").format.columnWidth = 14;
  sheet.getRange("I:I").format.columnWidth = 14;
  sheet.getRange("J:J").format.columnWidth = 52;
}

readme.getRange("A21:B25").values = [
  ["Calendar lookup", "Select the record whose calendar_id matches the employee and whose effective range contains the schedule-generation date. Missing dates are errors, not nulls."],
  ["School_Calendar", "Daily 2023–2028 NYCPS session classification. Full closures are false; attendance, remote-learning, early-dismissal and conference days are true."],
  ["Holiday_Calendar", "Federal and NY State holidays are independent records. A holiday does not alter school-session status."],
  ["Planning horizon", "Rows tagged is_assumption=true are 2027–28 placeholders that must be replaced when NYCPS/NYS publish official dates."],
  ["Calendar maintenance", "Append a corrected version and set effective_to on the superseded row; never overwrite historical classifications."],
];
readme.getRange("A21:B25").format.wrapText = true;
readme.getRange("A21:A25").format.font = { bold: true };
readme.getRange("A21:B25").format.verticalAlignment = "top";
readme.getRange("A21:A25").format.columnWidth = 22;
readme.getRange("B21:B25").format.columnWidth = 88;

workbook.recalculate();
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(workbookPath);
console.log(JSON.stringify({ schoolRows: schoolRows.length, holidayRows: holidayRows.length, workbookPath }));
