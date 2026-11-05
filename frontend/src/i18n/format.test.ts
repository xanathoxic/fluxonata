import { formatDate } from "./index";

describe("formatDate", () => {
  it("reads « 3 nov. 2026, 16:16 » (no seconds, month spelled out)", () => {
    const s = formatDate("2026-11-03T16:16:42");
    expect(s).toMatch(/^3 nov\.? 2026,? (à )?16:16$/);
  });
  it("shows a dash when there is no date", () => {
    expect(formatDate(null)).toBe("—");
  });
});

import { relativeDate } from "./index";
describe("relativeDate", () => {
  const now = Date.parse("2026-11-04T12:00:00Z");
  it("speaks in days, hours and weeks", () => {
    expect(relativeDate("2026-11-03T12:00:00Z", now)).toMatch(/hier/);
    expect(relativeDate("2026-11-01T12:00:00Z", now)).toMatch(/3 jours/);
    expect(relativeDate("2026-11-04T09:00:00Z", now)).toMatch(/3 heures/);
    expect(relativeDate("2026-10-14T12:00:00Z", now)).toMatch(/3 semaines/);
  });
  it("shows a dash without a date", () => { expect(relativeDate(null)).toBe("—"); });
});
