/** Page numbers with ellipses, previous / next and a page-size choice. 1-based pages; shows « 26–50 sur 130 ». */
export function Pagination({ total, page, pageSize, onPage, onPageSize, sizes = [10, 25, 50, 100], label = "Pagination" }: {
  total: number; page: number; pageSize: number; onPage: (p: number) => void; onPageSize: (n: number) => void; sizes?: number[]; label?: string;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const cur = Math.min(page, pages);
  const from = total === 0 ? 0 : (cur - 1) * pageSize + 1;
  const to = Math.min(total, cur * pageSize);
  const nums: (number | "…")[] = [];
  for (let p = 1; p <= pages; p++) {
    if (p === 1 || p === pages || Math.abs(p - cur) <= 1) nums.push(p);
    else if (nums[nums.length - 1] !== "…") nums.push("…");
  }
  return (
    <nav className="pager" aria-label={label}>
      <span className="pager-info" role="status">{from}–{to} sur {total}</span>
      <div className="pager-nums">
        <button type="button" onClick={() => onPage(cur - 1)} disabled={cur <= 1} aria-label="Page précédente">‹</button>
        {nums.map((n, i) => n === "…"
          ? <span key={`e${i}`} className="pager-gap" aria-hidden="true">…</span>
          : <button key={n} type="button" className={n === cur ? "pager-cur" : ""} aria-current={n === cur ? "page" : undefined} aria-label={`Page ${n}`} onClick={() => onPage(n)}>{n}</button>)}
        <button type="button" onClick={() => onPage(cur + 1)} disabled={cur >= pages} aria-label="Page suivante">›</button>
      </div>
      <label className="pager-size">Par page
        <select value={pageSize} onChange={(e) => onPageSize(Number(e.target.value))}>{sizes.map((s) => <option key={s} value={s}>{s}</option>)}</select>
      </label>
    </nav>
  );
}
