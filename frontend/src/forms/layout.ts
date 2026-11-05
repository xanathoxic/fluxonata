import { isBlock, type FieldType, type FormDefinition, type FormField, type Layout } from "./types";

/** Placement of fields on the 12-column grid of a section. A field covers `span` columns and `height` rows. The reading order of
 * a form is its layout order: row, then column. The server applies the same rules (`triggers/formengine.py`: `placed`,
 * `_check_layout`), so what the editor shows is what visibility rules and the submission see. */
export const COLS = 12;
export const MAX_HEIGHT = 8;
export const FULL: Pick<Layout, "col" | "span"> = { col: 1, span: COLS };
/** Types that are naturally taller than one row: a new field of these types starts with that height (a long text is worth two short ones). */
export const DEFAULT_HEIGHT: Partial<Record<FieldType, number>> = { textarea: 2, datagrid: 3 };

export const heightOf = (f: Pick<FormField, "layout">): number => f.layout?.height ?? 1;
const bottom = (l: Layout) => l.row + (l.height ?? 1) - 1;
const overlaps = (a: Layout, b: Layout) => a.row <= bottom(b) && b.row <= bottom(a) && a.col <= b.col + b.span - 1 && b.col <= a.col + a.span - 1;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Every field with a complete layout: a field without one takes a row of its own below the placed ones, in list order. A
 * naturally tall field with no explicit height (an older form) takes its usual height when the rows below it are free. */
export function normalize(fields: FormField[]): FormField[] {
  let last = fields.reduce((m, f) => (f.layout ? Math.max(m, bottom(f.layout)) : m), 0);
  const placed = fields.map((f) => (f.layout ? f : { ...f, layout: { row: ++last, ...FULL } }));
  return placed.map((f, i) => {
    const d = DEFAULT_HEIGHT[f.type];
    const l = f.layout as Layout;
    if (!d || l.height !== undefined) return f;
    const wanted = { ...l, height: d };
    return placed.some((o, k) => k !== i && overlaps(wanted, o.layout as Layout)) ? f : { ...f, layout: wanted };
  });
}

const compare = (a: { f: FormField; i: number }, b: { f: FormField; i: number }) => {
  const x = a.f.layout as Layout, y = b.f.layout as Layout;
  return x.row - y.row || x.col - y.col || a.i - b.i;
};

/** Fields in reading order (row, column), with their placement filled in. */
export function readingOrder(fields: FormField[]): FormField[] {
  return normalize(fields).map((f, i) => ({ f, i })).sort(compare).map((t) => t.f);
}

/** All the fields of a form in reading order: sections as listed, each one row by row. */
export const orderedFields = (d: FormDefinition): FormField[] => d.sections.flatMap((s) => readingOrder(s.fields));
export const dataFields = (d: FormDefinition): FormField[] => orderedFields(d).filter((f) => !isBlock(f.type));

/** Rows renumbered 1…n without holes: only the rows no field covers disappear, so a tall field keeps its rows together. */
function compact(fields: FormField[]): FormField[] {
  const covered = new Set<number>();
  for (const f of fields) { const l = f.layout as Layout; for (let r = l.row; r <= bottom(l); r++) covered.add(r); }
  const order = [...covered].sort((a, b) => a - b);
  return fields.map((f) => ({ ...f, layout: { ...(f.layout as Layout), row: order.indexOf((f.layout as Layout).row) + 1 } }));
}

/** The height is written when it is not the obvious one: above 1, or for a type that is tall by default (so that choosing 1 sticks). */
function finalize(fields: FormField[]): FormField[] {
  return fields.map((f) => {
    const { height, ...rest } = f.layout as Layout;
    const keep = (height ?? 1) > 1 || (DEFAULT_HEIGHT[f.type] !== undefined && height !== undefined);
    return { ...f, layout: keep ? { ...rest, height: height as number } : rest };
  });
}

export const rowCount = (fields: FormField[]): number => normalize(fields).reduce((m, f) => Math.max(m, bottom(f.layout as Layout)), 0);

/** Moves (or resizes) one field. The target is clamped to the grid. When it lands on another field, it takes a band of rows of its
 * own right there instead (the rows below shift down) and never cuts a tall field in two: nothing is ever overwritten or lost.
 * Making a field taller pushes down only what is under it. The result is in reading order. */
export function place(fields: FormField[], key: string, target: Layout): FormField[] {
  const all = normalize(fields);
  const mover = all.find((f) => f.key === key) as FormField;
  const old = mover.layout as Layout;
  const span = clamp(target.span, 1, COLS);
  const height = clamp(target.height ?? heightOf(mover), 1, MAX_HEIGHT);
  const mine: Layout = { row: Math.max(1, target.row), col: clamp(target.col, 1, COLS - span + 1), span, height };
  const others = all.filter((f) => f.key !== key);
  const clash = others.some((f) => overlaps(mine, f.layout as Layout));
  const straddles = (at: number) => others.some((f) => { const l = f.layout as Layout; return l.row < at && at <= bottom(l); });

  const grewOnly = mine.row === old.row && mine.col === old.col && mine.span === old.span && height > heightOf(mover);
  let from = mine.row, by = height; // rows from `from` down shift by `by`
  if (clash && grewOnly && !straddles(bottom(old) + 1)) { from = bottom(old) + 1; by = height - heightOf(mover); }
  else if (clash) { while (straddles(from)) from = Math.min(...others.filter((f) => (f.layout as Layout).row < from && from <= bottom(f.layout as Layout)).map((f) => bottom(f.layout as Layout) + 1)); }

  const moved = all.map((f) => {
    if (f.key === key) return { ...f, layout: { ...mine, row: clash && !grewOnly ? from : mine.row } };
    const l = f.layout as Layout;
    return clash && l.row >= from ? { ...f, layout: { ...l, row: l.row + by } } : f;
  });
  return finalize(readingOrder(compact(moved)));
}

/** A new field on a row of its own, at the bottom of the section (with the height its type usually needs). */
export function append(fields: FormField[], field: FormField): FormField[] {
  const h = DEFAULT_HEIGHT[field.type];
  return finalize([...readingOrder(fields), { ...field, layout: { row: rowCount(fields) + 1, ...FULL, ...(h ? { height: h } : {}) } }]);
}

/** Rows used by the fields that are shown, renumbered without holes (a hidden row leaves no empty space). */
export function visibleRows(fields: FormField[]): Map<string, Layout> {
  const shown = fields.map((f) => ({ key: f.key, l: f.layout as Layout }));
  const covered = new Set<number>();
  for (const { l } of shown) for (let r = l.row; r <= bottom(l); r++) covered.add(r);
  const order = [...covered].sort((a, b) => a - b);
  return new Map(shown.map(({ key, l }) => [key, { ...l, row: order.indexOf(l.row) + 1 }]));
}
