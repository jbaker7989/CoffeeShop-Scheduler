import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const dataDir = process.env.COFFEESHOP_DATA_DIR ?? new URL(".", import.meta.url).pathname;
const workbookPath = new URL("coffee_shop_labor_rules.xlsx", `file://${dataDir.endsWith("/") ? dataDir : `${dataDir}/`}`).pathname;
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
const school = workbook.worksheets.getItem("School_Calendar");
const holidays = workbook.worksheets.getItem("Holiday_Calendar");
const schoolRows = school.getUsedRange().values.slice(1).filter((row) => row.some((value) => value !== null && value !== ""));
const holidayRows = holidays.getUsedRange().values.slice(1).filter((row) => row.some((value) => value !== null && value !== ""));
const iso = (value) => value instanceof Date ? value.toISOString().slice(0, 10) : new Date(value).toISOString().slice(0, 10);
const expected = [];
for (let cursor = new Date("2023-01-01T00:00:00Z"); cursor <= new Date("2028-12-31T00:00:00Z"); cursor.setUTCDate(cursor.getUTCDate() + 1)) expected.push(cursor.toISOString().slice(0, 10));
const activeByDate = new Map();
for (const row of schoolRows) {
  const date = iso(row[1]);
  if (row[7] === null || row[7] === "" || new Date(row[7]) > new Date("2026-11-12T00:00:00Z")) activeByDate.set(date, (activeByDate.get(date) ?? 0) + 1);
}
const missing = expected.filter((date) => activeByDate.get(date) !== 1);
const rosterKey = schoolRows.some((row) => row[0] === "NYC-DOE-2026-27");
const assumptions = schoolRows.filter((row) => row[8] === true).length;
const invalidHolidayRows = holidayRows.filter((row) => !row[0] || !row[1] || !row[3] || !row[4] || !row[5]);
const checks = { expectedSchoolRows: expected.length, actualSchoolRows: schoolRows.length, missingOrDuplicateActiveDates: missing, hasRosterCalendarKey: rosterKey, planningAssumptionRows: assumptions, holidayRows: holidayRows.length, invalidHolidayRows: invalidHolidayRows.length };
if (missing.length || !rosterKey || invalidHolidayRows.length || checks.actualSchoolRows !== checks.expectedSchoolRows) throw new Error(JSON.stringify(checks));
console.log(JSON.stringify(checks, null, 2));
