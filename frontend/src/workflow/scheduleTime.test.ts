import { describe, expect, it } from "vitest";
import { relative, splitDelay, tomorrowAt8, validZone, zonedToUtc } from "./scheduleTime";

const iso = (ms: number | null) => (ms === null ? null : new Date(ms).toISOString());

describe("dates du nœud Programmer", () => {
  it("convertit une heure murale en instant UTC, hiver et été (comme le serveur)", () => {
    expect(iso(zonedToUtc("2026-12-01T08:30", "Europe/Paris"))).toBe("2026-12-01T07:30:00.000Z"); // UTC+1
    expect(iso(zonedToUtc("2026-07-01T08:30", "Europe/Paris"))).toBe("2026-07-01T06:30:00.000Z"); // UTC+2
    expect(iso(zonedToUtc("2026-12-01T08:30", "UTC"))).toBe("2026-12-01T08:30:00.000Z");
    expect(iso(zonedToUtc("2026-12-01T08:30", "America/New_York"))).toBe("2026-12-01T13:30:00.000Z");
    expect(iso(zonedToUtc("2026-12-01 08:30:15", "Asia/Tokyo"))).toBe("2026-11-30T23:30:15.000Z");
  });
  it("gère le jour du passage à l'heure d'été", () => {
    expect(iso(zonedToUtc("2027-03-28T12:00", "Europe/Paris"))).toBe("2027-03-28T10:00:00.000Z");
    expect(iso(zonedToUtc("2027-03-27T12:00", "Europe/Paris"))).toBe("2027-03-27T11:00:00.000Z");
  });
  it("refuse ce qui n'est pas une date et une heure, ou un fuseau inconnu", () => {
    expect(zonedToUtc("2026-12-01", "UTC")).toBeNull();
    expect(zonedToUtc("demain", "UTC")).toBeNull();
    expect(zonedToUtc("2026-12-01T08:30", "Mars/Olympus")).toBeNull();
    expect(validZone("Europe/Paris")).toBe(true);
    expect(validZone("Mars/Olympus")).toBe(false);
  });
  it("dit dans combien de temps, ou que la date est passée", () => {
    const now = Date.UTC(2026, 11, 1, 12, 0, 0);
    expect(relative(now - 1000, now)).toBe("déjà passée");
    expect(relative(now + 20_000, now)).toBe("dans moins d'une minute");
    expect(relative(now + 90 * 60_000, now)).toBe("dans 1 h 30 min");
    expect(relative(now + 26 * 3600_000, now)).toBe("dans 1 jour 2 h");
    expect(relative(now + 3 * 86400_000, now)).toBe("dans 3 jours");
  });
  it("choisit la plus grande unité qui tombe juste pour un délai", () => {
    expect(splitDelay(7200)).toEqual({ value: 2, unit: 3600 });
    expect(splitDelay(86400 * 3)).toEqual({ value: 3, unit: 86400 });
    expect(splitDelay(90)).toEqual({ value: 90, unit: 1 });
    expect(splitDelay(120)).toEqual({ value: 2, unit: 60 });
    expect(splitDelay(undefined)).toEqual({ value: "", unit: 3600 });
  });
  it("propose demain 08:00 pour une nouvelle étape", () => {
    expect(tomorrowAt8(new Date(2026, 11, 31, 22, 0))).toBe("2027-01-01T08:00");
    expect(tomorrowAt8(new Date(2026, 1, 28, 9, 0))).toBe("2026-03-01T08:00");
  });
});
