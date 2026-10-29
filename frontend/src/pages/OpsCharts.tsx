import { useEffect, useId, useRef, useState } from "react";

export interface DayPoint { day: string; succeeded: number; failed: number }

const H = 170, ML = 30, MR = 6, MT = 8, MB = 22;

/** The width of an element, followed as it resizes: the plot is drawn at its real size, so text and bars keep their size. */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(640);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => setW(Math.max(280, Math.round(el.getBoundingClientRect().width)));
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}
const niceMax = (v: number) => { const p = 10 ** Math.floor(Math.log10(Math.max(1, v))); const n = v / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; };
const shortDay = (d: string) => `${d.slice(8, 10)}/${d.slice(5, 7)}`;
const longDay = (d: string) => new Date(`${d}T12:00:00`).toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "long" });
/** A rectangle whose top corners are rounded (the data end); the base stays square, anchored to the axis. */
const topRounded = (x: number, y: number, w: number, h: number, r: number) => {
  const k = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + k}Q${x},${y} ${x + k},${y}H${x + w - k}Q${x + w},${y} ${x + w},${y + k}V${y + h}Z`;
};

/** Runs per day, stacked: succeeded (bottom) and failed (top, hatched so that colour is never the only cue). One y-axis,
 *  faint grid, a hover/focus tooltip per day, a legend with totals and a table view for screen readers and printing. */
export function RunsChart({ data }: { data: DayPoint[] }) {
  const id = useId().replace(/:/g, "");
  const [hover, setHover] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const [ref, W] = useWidth<HTMLDivElement>();
  const totals = data.reduce((a, p) => ({ ok: a.ok + p.succeeded, ko: a.ko + p.failed }), { ok: 0, ko: 0 });
  const top = niceMax(Math.max(1, ...data.map((p) => p.succeeded + p.failed)));
  const iw = W - ML - MR, ih = H - MT - MB, band = iw / Math.max(1, data.length), bw = Math.min(34, band * 0.62);
  const y = (v: number) => MT + ih - (ih * v) / top;
  const step = Math.ceil(data.length / Math.max(2, Math.floor(W / 54)));
  const ticks = [0, 1, 2, 3].map((i) => Math.round((top * i) / 3));
  const h = hover === null ? null : data[hover];
  return (
    <div className="viz">
      <div className="viz-head">
        <ul className="viz-legend" aria-label="Légende">
          <li><span className="sw sw-ok" aria-hidden="true" />Réussies <strong>{totals.ok}</strong></li>
          <li><span className="sw sw-ko" aria-hidden="true" />Échouées <strong>{totals.ko}</strong></li>
        </ul>
        <button type="button" className="viz-toggle" aria-pressed={table} onClick={() => setTable(!table)}>{table ? "Afficher le graphique" : "Afficher en tableau"}</button>
      </div>
      {table ? (
        <div className="table-wrap"><table><caption className="sr-only">Exécutions terminées par jour</caption>
          <thead><tr><th scope="col">Jour</th><th scope="col">Réussies</th><th scope="col">Échouées</th><th scope="col">Taux d'échec</th></tr></thead>
          <tbody>{data.map((p) => <tr key={p.day}><th scope="row">{longDay(p.day)}</th><td>{p.succeeded}</td><td>{p.failed}</td><td>{p.succeeded + p.failed ? `${Math.round((100 * p.failed) / (p.succeeded + p.failed))} %` : "—"}</td></tr>)}</tbody></table></div>
      ) : (
        <div className="viz-plot" ref={ref}>
          <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="group" aria-label={`Exécutions terminées par jour : ${totals.ok} réussies et ${totals.ko} échouées sur ${data.length} jour(s)`} className="viz-svg">
            <defs>
              <pattern id={`${id}h`} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="var(--err)" /><line x1="0" y1="0" x2="0" y2="6" stroke="#fff" strokeOpacity=".45" strokeWidth="2" /></pattern>
            </defs>
            {ticks.map((t) => (
              <g key={t}><line x1={ML} x2={W - MR} y1={y(t)} y2={y(t)} className={t === 0 ? "viz-axis" : "viz-grid"} /><text x={ML - 6} y={y(t) + 4} textAnchor="end" className="viz-tick">{t}</text></g>
            ))}
            {data.map((p, i) => {
              const cx = ML + band * i + band / 2, x = cx - bw / 2;
              const hOk = (ih * p.succeeded) / top, hKo = (ih * p.failed) / top;
              return (
                <g key={p.day}>
                  {hover === i && <rect x={ML + band * i} y={MT} width={band} height={ih} className="viz-hl" />}
                  {p.succeeded > 0 && <path d={p.failed > 0 ? `M${x},${y(0)}V${y(p.succeeded)}H${x + bw}V${y(0)}Z` : topRounded(x, y(p.succeeded), bw, hOk, 4)} fill="var(--ok)" stroke="var(--surface)" strokeWidth="2" />}
                  {p.failed > 0 && <path d={topRounded(x, y(p.succeeded + p.failed), bw, hKo, 4)} fill={`url(#${id}h)`} stroke="var(--surface)" strokeWidth="2" />}
                  {i % step === 0 && <text x={cx} y={H - 6} textAnchor="middle" className="viz-tick">{shortDay(p.day)}</text>}
                  <rect x={ML + band * i} y={MT} width={band} height={ih} fill="transparent" tabIndex={0} role="img" aria-label={`${longDay(p.day)} : ${p.succeeded} réussie(s), ${p.failed} échouée(s)`}
                    onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(i)} onBlur={() => setHover(null)} className="viz-hit" />
                </g>
              );
            })}
          </svg>
          {h && hover !== null && (
            <div className="viz-tip" role="presentation" style={{ left: `${((ML + band * hover + band / 2) / W) * 100}%` }}>
              <strong>{longDay(h.day)}</strong>
              <div><span className="sw sw-ok" aria-hidden="true" />{h.succeeded} réussie(s)</div>
              <div><span className="sw sw-ko" aria-hidden="true" />{h.failed} échouée(s)</div>
              {h.succeeded + h.failed > 0 && <div className="hint">{Math.round((100 * h.failed) / (h.succeeded + h.failed))} % d'échec</div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export interface BarRow { key: string; label: string; sub?: string; value: number; shown: string; tone?: "bad" | "accent" }

/** Horizontal bars for a ranking: one measure, one scale, the value written at the end of each bar. */
export function HBars({ rows, label, empty }: { rows: BarRow[]; label: string; empty: string }) {
  if (rows.length === 0) return <p className="hint viz-empty">{empty}</p>;
  const max = Math.max(1e-9, ...rows.map((r) => r.value));
  return (
    <ul className="hbars" aria-label={label}>
      {rows.map((r) => (
        <li key={r.key}>
          <div className="hb-top"><span className="hb-label" title={r.sub ? `${r.label} — ${r.sub}` : r.label}>{r.label}{r.sub && <span className="hb-sub"> · {r.sub}</span>}</span><span className="hb-val">{r.shown}</span></div>
          <div className="hb-track" aria-hidden="true"><div className={`hb-fill ${r.tone === "bad" ? "hb-bad" : ""}`} style={{ width: `${Math.max(3, (100 * r.value) / max)}%` }} /></div>
        </li>
      ))}
    </ul>
  );
}

/** A headline figure: the number is the content, the tile only frames it. */
export function Kpi({ label, value, hint, tone, share }: { label: string; value: string | number; hint?: string; tone?: "ok" | "bad" | "info"; share?: number | null }) {
  return (
    <li className={`kpi ${tone ? `kpi-${tone}` : ""}`}>
      <span className="kpi-l">{label}</span>
      <span className="kpi-n">{value}</span>
      {share !== undefined && share !== null && <span className="kpi-bar" aria-hidden="true"><span style={{ width: `${Math.max(0, Math.min(100, share))}%` }} /></span>}
      {hint && <span className="kpi-h">{hint}</span>}
    </li>
  );
}
