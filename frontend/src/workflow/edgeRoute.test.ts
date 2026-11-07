import { describe, expect, it } from "vitest";
import { cleanPoints, labelPoint, MAX_POINTS, roundedPath, segmentMidpoints } from "./edgeRoute";

describe("edge route", () => {
  it("draws a straight line when there is no point, and goes through every point", () => {
    expect(roundedPath([{ x: 0, y: 0 }, { x: 100, y: 0 }])).toBe("M 0 0 L 100 0");
    const d = roundedPath([{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 100 }]);
    expect(d).toContain("Q 100 0 100 10"); // the corner at (100, 0) is rounded, not cut off
    expect(d.endsWith("L 100 100")).toBe(true);
  });

  it("rounds a corner less when a segment is short, and ignores duplicate points", () => {
    expect(roundedPath([{ x: 0, y: 0 }, { x: 6, y: 0 }, { x: 6, y: 100 }])).toContain("Q 6 0 6 3"); // radius 10 → half of 6
    expect(roundedPath([{ x: 0, y: 0 }, { x: 0, y: 0 }, { x: 50, y: 0 }])).toBe("M 0 0 L 50 0");
    expect(roundedPath([])).toBe("");
  });

  it("gives the middle of each segment, and puts the label on the longest one", () => {
    const chain = [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 10, y: 100 }];
    expect(segmentMidpoints(chain)).toEqual([{ x: 5, y: 0 }, { x: 10, y: 50 }]);
    expect(labelPoint(chain)).toEqual({ x: 10, y: 50 });
  });

  it("keeps whole, valid, distinct points and at most MAX_POINTS", () => {
    expect(cleanPoints([{ x: 1.4, y: 2.6 }, { x: 1, y: 3 }, { x: Number.NaN, y: 0 }, { x: 5, y: 5 }])).toEqual([{ x: 1, y: 3 }, { x: 5, y: 5 }]);
    expect(cleanPoints(Array.from({ length: 40 }, (_, i) => ({ x: i, y: 0 }))).length).toBe(MAX_POINTS);
  });
});
