import { useEffect, useMemo, useState, type ReactNode } from "react";
import { HelpTip } from "./HelpTip";
import { Overlay } from "./Overlay";
import { Pagination } from "./Pagination";

export type Tone = "ok" | "warn" | "bad" | "info" | "muted";

/** A status or role chip. The text always says what the colour says. */
export function Badge({ tone = "muted", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`adm-badge adm-badge-${tone}`}>{children}</span>;
}

const HUES = [186, 221, 262, 316, 28, 152, 340, 45];
/** Initials in a coloured disc (decorative: the name is always written next to it). */
export function Avatar({ name }: { name: string }) {
  const hue = HUES[[...name].reduce((n, c) => n + c.charCodeAt(0), 0) % HUES.length] as number;
  const initials = name.replace(/[^\p{L}\p{N} _.-]/gu, "").split(/[ _.-]+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("") || "?";
  return <span className="adm-av" aria-hidden="true" style={{ background: `hsl(${hue} 55% 30%)` }}>{initials}</span>;
}

/** A card with a title, a one-line explanation and the actions of the section. */
export function Section({ title, hint, info, actions, children }: { title: string; hint?: ReactNode; info?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="adm-card">
      <header className="adm-card-h">
        <div><h2>{title}{hint && <HelpTip>{hint}</HelpTip>}</h2>{info && <p className="hint">{info}</p>}</div>
        {actions && <div className="adm-card-a">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

/** Client-side paging of an already loaded list; the page goes back to 1 when the list changes. */
export function usePaged<T>(rows: T[], initialSize = 10, sizes?: number[]) {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(initialSize);
  const pages = Math.max(1, Math.ceil(rows.length / size));
  useEffect(() => setPage(1), [rows.length]); // a new search or filter starts at the first page
  useEffect(() => { if (page > pages) setPage(pages); }, [page, pages]);
  const slice = useMemo(() => rows.slice((Math.min(page, pages) - 1) * size, Math.min(page, pages) * size), [rows, page, pages, size]);
  const pager = (label: string) => rows.length > 10
    ? <Pagination total={rows.length} page={page} pageSize={size} onPage={setPage} onPageSize={(n) => { setSize(n); setPage(1); }} label={label} sizes={sizes} />
    : null;
  return { slice, pager, reset: () => setPage(1) };
}

/** The search box above a table. */
export function SearchBox({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <div className="adm-search">
      <label htmlFor={`s-${label}`} className="sr-only">{label}</label>
      <span aria-hidden="true">⌕</span>
      <input id={`s-${label}`} type="search" value={value} placeholder={placeholder ?? label} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

/** « Are you sure? » in a small dialog, for actions that are hard to undo. */
export function Confirm({ title, children, confirmLabel, danger = true, busy, onConfirm, onCancel }: {
  title: string; children: ReactNode; confirmLabel: string; danger?: boolean; busy?: boolean; onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <Overlay variant="modal" title={title} onClose={onCancel}
      footer={<><button type="button" onClick={onCancel}>Annuler</button><button type="button" className={danger ? "danger" : "primary"} disabled={busy} onClick={onConfirm}>{confirmLabel}</button></>}>
      {children}
    </Overlay>
  );
}

export function EmptyRow({ cols, children }: { cols: number; children: ReactNode }) {
  return <tr><td colSpan={cols} className="adm-empty">{children}</td></tr>;
}

/** Sorting of a loaded list: click a column header to sort, again to reverse. */
export function useSort<T>(rows: T[], keys: Record<string, (r: T) => string | number>, initial: { key: string; dir: "asc" | "desc" }) {
  const [sort, setSort] = useState(initial);
  const sorted = useMemo(() => {
    const get = keys[sort.key];
    if (!get) return rows;
    const out = [...rows].sort((a, b) => {
      const x = get(a), y = get(b);
      return typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y), "fr", { numeric: true, sensitivity: "base" });
    });
    return sort.dir === "desc" ? out.reverse() : out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, sort]);
  const toggle = (key: string) => setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));
  return { sorted, sort, toggle };
}

/** A column header that sorts the table. The button is the header's name, so it stays readable for a screen reader. */
export function SortTh({ label, field, sort, onSort }: { label: string; field: string; sort: { key: string; dir: "asc" | "desc" }; onSort: (k: string) => void }) {
  const on = sort.key === field;
  return (
    <th scope="col" aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className="adm-sort" onClick={() => onSort(field)}>{label}<span aria-hidden="true">{on ? (sort.dir === "asc" ? " ▲" : " ▼") : " ↕"}</span></button>
    </th>
  );
}
