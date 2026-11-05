import { describe, expect, it } from "vitest";
import { append, normalize, orderedFields, place, readingOrder, rowCount, visibleRows } from "./layout";
import { contrastWithWhite, styleProps } from "./style";
import type { FormField } from "./types";

const f = (key: string, row?: number, col = 1, span = 12): FormField => ({ key, label: key, type: "text", ...(row ? { layout: { row, col, span } } : {}) });
const keys = (fs: FormField[]) => fs.map((x) => x.key);
const lay = (fs: FormField[], key: string) => fs.find((x) => x.key === key)?.layout;

describe("reading order (same cases as the server)", () => {
  it("is row, then column; unplaced fields take rows after the placed ones, in list order", () => {
    const d = [f("c", 2), f("loose"), f("b", 1, 7, 6), f("a", 1, 1, 6), f("loose2")];
    expect(keys(readingOrder(d))).toEqual(["a", "b", "c", "loose", "loose2"]);
    expect(lay(normalize(d), "loose")).toEqual({ row: 3, col: 1, span: 12 });
    expect(lay(normalize(d), "loose2")).toEqual({ row: 4, col: 1, span: 12 });
  });
  it("orders sections as listed", () => {
    const d = { sections: [{ title: "1", fields: [f("z", 1)] }, { title: "2", fields: [f("a", 1)] }] };
    expect(keys(orderedFields(d))).toEqual(["z", "a"]);
  });
});

describe("place", () => {
  const base = () => [f("a", 1, 1, 6), f("b", 1, 7, 6), f("c", 2)];
  it("moves a field to a free spot, clamps to the grid and compacts rows", () => {
    const r = place(base(), "c", { row: 1, col: 13, span: 12 }); // lands on a and b: takes its own row 1, the others go down
    expect(lay(r, "c")).toEqual({ row: 1, col: 1, span: 12 });
    expect(lay(r, "a")?.row).toBe(2);
    const free = place([f("a", 1, 1, 4), f("b", 2)], "b", { row: 1, col: 5, span: 4 });
    expect(lay(free, "b")).toEqual({ row: 1, col: 5, span: 4 });
    expect(rowCount(free)).toBe(1); // the emptied row disappears
  });
  it("a collision inserts a row instead of overwriting", () => {
    const r = place(base(), "c", { row: 1, col: 3, span: 2 });
    expect(keys(r)).toEqual(["c", "a", "b"]);
    expect(lay(r, "a")).toEqual({ row: 2, col: 1, span: 6 });
    expect(lay(r, "b")).toEqual({ row: 2, col: 7, span: 6 });
  });
  it("resizes within the row", () => {
    const r = place(base(), "a", { row: 1, col: 1, span: 3 });
    expect(lay(r, "a")?.span).toBe(3);
    expect(lay(r, "b")).toEqual({ row: 1, col: 7, span: 6 });
    expect(lay(place(base(), "a", { row: 1, col: 1, span: 9 }), "b")?.row).toBeGreaterThan(1); // grew over b: b gets its own row
  });
  it("keeps the list in reading order and never loses a field", () => {
    const r = place(base(), "a", { row: 3, col: 1, span: 12 });
    expect(keys(r)).toEqual(["b", "c", "a"]);
    expect(r).toHaveLength(3);
  });
  it("appends on a row of its own", () => {
    const r = append(base(), f("d"));
    expect(lay(r, "d")).toEqual({ row: 3, col: 1, span: 12 });
  });
});

describe("hidden rows leave no hole", () => {
  it("renumbers the rows of the shown fields", () => {
    const m = visibleRows([f("a", 1), f("c", 3, 1, 6), f("d", 3, 7, 6)].map((x) => x));
    expect(m.get("c")?.row).toBe(2);
    expect(m.get("d")?.row).toBe(2);
  });
});

describe("style", () => {
  it("measures contrast like the server", () => {
    expect(Math.round(contrastWithWhite("#000000"))).toBe(21);
    expect(contrastWithWhite("#0e7490")).toBeGreaterThan(4.5);
    expect(contrastWithWhite("#ffff00")).toBeLessThan(4.5);
  });
  it("only lets a readable colour and the closed choices through", () => {
    expect(styleProps({ accent: "#1d4ed8" }).style).toMatchObject({ "--accent": "#1d4ed8" });
    expect(styleProps({ accent: "#ffff00" }).style).not.toHaveProperty("--accent");
    expect(styleProps({ accent: "red; background:url(x)" }).style).not.toHaveProperty("--accent");
    expect(styleProps({ width: "narrow", density: "compact", labels: "left" })).toMatchObject({ className: expect.stringContaining("fb-l-left"), style: { maxWidth: "480px" } });
  });
});

const area = (key: string, row?: number, col = 1, span = 12, height?: number): FormField => ({ key, label: key, type: "textarea", ...(row ? { layout: { row, col, span, ...(height ? { height } : {}) } } : {}) });

describe("heights: a long text is worth two short ones", () => {
  it("starts a new long field at two rows, a short one at one", () => {
    expect(lay(append([], area("t")), "t")).toEqual({ row: 1, col: 1, span: 12, height: 2 });
    expect(lay(append([], f("a")), "a")).toEqual({ row: 1, col: 1, span: 12 });
  });
  it("gives an older long field its usual height only when the rows below are free", () => {
    expect(lay(normalize([area("t", 1), f("a", 3)]), "t")?.height).toBe(2); // row 2 is free
    expect(lay(normalize([area("t", 1), f("a", 2)]), "t")?.height).toBeUndefined(); // a sits right under it: unchanged
    expect(lay(normalize([area("t", 1, 1, 6), f("a", 2, 7, 6)]), "t")?.height).toBe(2); // beside it, not under it
  });
  it("lets short fields stack beside a tall one: no wasted space", () => {
    const start = [f("a", 1, 1, 6), area("t", 1, 7, 6, 2), f("b", 3, 1, 12)];
    const r = place(start, "b", { row: 2, col: 1, span: 6 });
    expect(lay(r, "b")).toEqual({ row: 2, col: 1, span: 6 });
    expect(lay(r, "t")).toEqual({ row: 1, col: 7, span: 6, height: 2 });
    expect(rowCount(r)).toBe(2);
  });
  it("never cuts a tall field in two when something lands on its lower row", () => {
    const start = [area("t", 1, 1, 12, 2), f("a", 3, 1, 12), f("m", 4, 1, 12)];
    const r = place(start, "m", { row: 2, col: 1, span: 12 }); // lands on the second row of t
    expect(lay(r, "t")).toEqual({ row: 1, col: 1, span: 12, height: 2 });
    expect(lay(r, "m")?.row).toBe(3); // below the whole field
    expect(lay(r, "a")?.row).toBe(4);
  });
  it("makes a field taller by pushing down only what is under it", () => {
    const start = [f("n", 1, 1, 6), area("t", 1, 7, 6, 1), f("u", 2, 7, 6), f("z", 3, 1, 12)];
    const r = place(start, "t", { row: 1, col: 7, span: 6, height: 2 });
    expect(lay(r, "t")).toEqual({ row: 1, col: 7, span: 6, height: 2 });
    expect(lay(r, "n")).toEqual({ row: 1, col: 1, span: 6 }); // its neighbour does not move
    expect(lay(r, "u")?.row).toBe(3);
    expect(lay(r, "z")?.row).toBe(4);
  });
  it("keeps a chosen height of 1 on a long field, and writes no height on a short one", () => {
    const r = place([area("t", 1, 1, 12, 2)], "t", { row: 1, col: 1, span: 12, height: 1 });
    expect(lay(r, "t")).toEqual({ row: 1, col: 1, span: 12, height: 1 });
    expect(lay(place([f("a", 1)], "a", { row: 1, col: 1, span: 6, height: 1 }), "a")).toEqual({ row: 1, col: 1, span: 6 });
    expect(lay(place([f("a", 1)], "a", { row: 1, col: 1, span: 6, height: 3 }), "a")?.height).toBe(3);
    expect(lay(place([f("a", 1)], "a", { row: 1, col: 1, span: 6, height: 99 }), "a")?.height).toBe(8); // clamped
  });
  it("closes only the rows nobody covers", () => {
    const r = place([area("t", 1, 1, 12, 3), f("a", 4)], "a", { row: 9, col: 1, span: 12 });
    expect(lay(r, "t")?.height).toBe(3);
    expect(lay(r, "a")?.row).toBe(4);
  });
  it("numbers the rows of the shown fields without holes, tall fields included", () => {
    const m = visibleRows([area("t", 1, 1, 6, 2), f("c", 5, 1, 12)]);
    expect(m.get("t")).toMatchObject({ row: 1, height: 2 });
    expect(m.get("c")?.row).toBe(3);
  });
});
