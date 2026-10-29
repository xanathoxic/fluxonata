import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";

export const MAX_TAGS = 10;
const OK = /^[\p{L}\p{N}][\p{L}\p{N}_.-]{0,29}$/u;

/** Same normalisation as the server: lowercase, spaces become « - ». Returns null when the word is not a valid tag. */
export function normalizeTag(raw: string): string | null {
  const t = raw.trim().toLowerCase().replace(/\s+/g, "-");
  return OK.test(t) ? t : null;
}

/** Tags of an object as small badges; with `onPick` each one filters the list it is shown in. */
export function TagList({ tags, onPick, active }: { tags?: string[]; onPick?: (t: string) => void; active?: string }) {
  if (!tags?.length) return null;
  return (
    <span className="tags">
      {tags.map((t) => onPick
        ? <button type="button" key={t} className="tagchip" aria-pressed={active === t} onClick={() => onPick(t)} title={`Filtrer sur « ${t} »`}>{t}</button>
        : <span key={t} className="tagchip">{t}</span>)}
    </span>
  );
}

/** Filter a list on one tag: a compact « Étiquettes » button opens a searchable list (with counts) instead of a wall of chips.
 *  The tag in use shows next to the button as a chip with a × to clear it. */
export function TagFilter({ lists, value, onChange }: { lists: (string[] | undefined)[]; value: string; onChange: (t: string) => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const btn = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const id = useId();
  const all = useMemo(() => {
    const n = new Map<string, number>();
    for (const l of lists) for (const t of l ?? []) n.set(t, (n.get(t) ?? 0) + 1);
    return [...n.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "fr"));
  }, [lists]);
  const shown = all.filter(([t]) => t.includes(q.trim().toLowerCase()));
  const place = () => {
    const r = btn.current?.getBoundingClientRect();
    if (r) setPos({ top: r.bottom + 6, left: Math.max(12, Math.min(r.left, window.innerWidth - 292)) });
  };
  useEffect(() => {
    if (!open) return;
    place();
    const close = (e: Event) => { const t = e.target as Node; if (!pop.current?.contains(t) && !btn.current?.contains(t)) setOpen(false); };
    const key = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); btn.current?.focus(); } };
    document.addEventListener("mousedown", close); document.addEventListener("keydown", key); window.addEventListener("resize", place); window.addEventListener("scroll", place, true);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", key); window.removeEventListener("resize", place); window.removeEventListener("scroll", place, true); };
  }, [open]);
  if (all.length === 0) return null;
  return (
    <span className="tagfilter">
      <button ref={btn} type="button" className={`tagfilter-btn${value ? " on" : ""}`} aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => { setOpen((o) => !o); setQ(""); }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d="M20.6 13.4l-7.2 7.2a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z" /><circle cx="7.5" cy="7.5" r="1.2" fill="currentColor" /></svg> Étiquettes <span className="tagfilter-n">{all.length}</span> <span aria-hidden="true">▾</span>
      </button>
      {value && <span className="tagchip tagchip-on">{value}<button type="button" className="tag-x" aria-label={`Retirer le filtre ${value}`} onClick={() => onChange("")}>×</button></span>}
      {open && createPortal(
        <div ref={pop} id={id} role="dialog" aria-label="Filtrer par étiquette" className="tagfilter-pop" style={{ top: pos.top, left: pos.left }}>
          <input type="search" autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Chercher une étiquette…" aria-label="Chercher une étiquette" />
          <ul>
            {shown.map(([t, c]) => (
              <li key={t}><button type="button" aria-pressed={value === t} onClick={() => { onChange(value === t ? "" : t); setOpen(false); }}><span className="tagchip">{t}</span><span className="hint">{c}</span></button></li>
            ))}
            {shown.length === 0 && <li className="hint tagfilter-none">Aucune étiquette ne correspond.</li>}
          </ul>
          {value && <button type="button" className="tagfilter-clear" onClick={() => { onChange(""); setOpen(false); }}>Effacer le filtre</button>}
        </div>,
        document.body,
      )}
    </span>
  );
}

/** Edit the tags of an object: type a word then Enter or comma; click × to remove. Suggestions come from tags already used. */
export function TagInput({ value, onChange, suggestions = [], label = "Étiquettes", disabled }: { value: string[]; onChange: (v: string[]) => void; suggestions?: string[]; label?: string; disabled?: boolean }) {
  const id = useId();
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const add = (raw: string) => {
    const parts = raw.split(",").map((p) => p.trim()).filter(Boolean);
    if (parts.length === 0) { setText(""); return; }
    const next = [...value];
    for (const p of parts) {
      const t = normalizeTag(p);
      if (!t) { setError(`« ${p} » n'est pas une étiquette valide : des lettres, chiffres, - _ . (30 caractères au plus).`); setText(p); onChange(next); return; }
      if (!next.includes(t)) next.push(t);
    }
    if (next.length > MAX_TAGS) { setError(`${MAX_TAGS} étiquettes au plus.`); return; }
    setError(""); setText(""); onChange(next);
  };
  const key = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(text); }
    else if (e.key === "Backspace" && !text && value.length) onChange(value.slice(0, -1));
  };
  const free = suggestions.filter((s) => !value.includes(s));
  return (
    <div className="field tag-field">
      <label htmlFor={id}>{label}</label>
      <div className={`tag-input${disabled ? " is-disabled" : ""}`}>
        {value.map((t) => (
          <span key={t} className="tagchip">{t}{!disabled && <button type="button" className="tag-x" aria-label={`Retirer l'étiquette ${t}`} onClick={() => onChange(value.filter((x) => x !== t))}>×</button>}</span>
        ))}
        {!disabled && value.length < MAX_TAGS && (
          <input id={id} list={`${id}-l`} value={text} placeholder={value.length ? "" : "Ajouter une étiquette…"} onChange={(e) => { setText(e.target.value); if (error) setError(""); }} onKeyDown={key} onBlur={() => text.trim() && add(text)} aria-describedby={error ? `${id}-e` : undefined} aria-invalid={!!error} />
        )}
        <datalist id={`${id}-l`}>{free.map((s) => <option key={s} value={s} />)}</datalist>
      </div>
      {error && <p id={`${id}-e`} role="alert" className="field-error">{error}</p>}
    </div>
  );
}
