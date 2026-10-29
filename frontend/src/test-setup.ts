import "@testing-library/jest-dom/vitest";

// jsdom has no layout engine: CodeMirror measures text ranges, so give it harmless zero-size rectangles.
const emptyRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: function* () {} }) as unknown as DOMRectList;
Range.prototype.getClientRects = emptyRects;
Range.prototype.getBoundingClientRect = () => new DOMRect(0, 0, 0, 0);
