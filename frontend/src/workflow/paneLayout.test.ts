import { beforeEach, describe, expect, it } from "vitest";
import { DEFAULT_LAYOUT, LIMITS, clamp, gridColumns, loadLayout, saveLayout } from "./paneLayout";

describe("schema editor pane layout", () => {
  beforeEach(() => localStorage.clear());

  it("builds the grid for what is visible", () => {
    expect(gridColumns(DEFAULT_LAYOUT, false)).toBe("236px 12px minmax(0, 1fr)");
    expect(gridColumns(DEFAULT_LAYOUT, true)).toBe("236px 12px minmax(0, 1fr) 12px 400px");
    expect(gridColumns({ ...DEFAULT_LAYOUT, palOpen: false }, true)).toBe("minmax(0, 1fr) 12px 400px");
    expect(gridColumns({ ...DEFAULT_LAYOUT, panOpen: false }, true)).toBe("236px 12px minmax(0, 1fr)"); // hidden panel: no column
    expect(gridColumns({ ...DEFAULT_LAYOUT, palOpen: false, panOpen: false }, true)).toBe("minmax(0, 1fr)");
  });

  it("keeps widths inside their limits", () => {
    expect(clamp(10, LIMITS.pal.min, LIMITS.pal.max)).toBe(LIMITS.pal.min);
    expect(clamp(9999, LIMITS.pal.min, LIMITS.pal.max)).toBe(LIMITS.pal.max);
    expect(clamp(250.6, LIMITS.pal.min, LIMITS.pal.max)).toBe(251);
  });

  it("remembers the layout and survives garbage in storage", () => {
    saveLayout({ palW: 300, panW: 500, palOpen: false, panOpen: true });
    expect(loadLayout()).toEqual({ palW: 300, panW: 500, palOpen: false, panOpen: true });
    localStorage.setItem("orch.schema.panes", JSON.stringify({ palW: 5, panW: "x", palOpen: "no" }));
    expect(loadLayout()).toEqual({ palW: LIMITS.pal.min, panW: DEFAULT_LAYOUT.panW, palOpen: true, panOpen: true });
    localStorage.setItem("orch.schema.panes", "{not json");
    expect(loadLayout()).toEqual(DEFAULT_LAYOUT);
  });
});
