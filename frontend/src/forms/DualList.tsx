import { useMemo, useState, type KeyboardEvent, type MouseEvent } from "react";

export interface DualOption { value: string; label?: string; group?: string; disabled?: boolean }

interface Props {
  id: string;
  labelledBy?: string;
  describedBy?: string;
  options: DualOption[];
  /** the chosen values, IN ORDER (the order is part of the value) */
  value: string[];
  onChange: (next: string[]) => void;
  /** read-only or locked: nothing can be moved */
  disabled?: boolean;
  /** at most this many chosen */
  max?: number;
  invalid?: boolean;
  /** the titles of the two columns */
  availableLabel?: string;
  chosenLabel?: string;
}

const fold = (s: string) => s.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
const picked = (e: { target: EventTarget & HTMLSelectElement }) => Array.from(e.target.selectedOptions, (o) => o.value);

/**
 * A dual list: what is available on the left, what is chosen on the right, with a search on each side, buttons to move one or all of
 * them, and the means to put the chosen ones in order (the order is the value). Two real multiple-selection lists and buttons rather than
 * a drag and drop: it works with the keyboard and with a screen reader (Enter moves, Alt + arrow reorders).
 */
export function DualList({ id, labelledBy, describedBy, options, value, onChange, disabled, max, invalid, availableLabel, chosenLabel }: Props) {
  const leftTitle = availableLabel?.trim() || "Disponibles";
  const rightTitle = chosenLabel?.trim() || "Sélectionnés";
  const [searchAvailable, setSearchAvailable] = useState("");
  const [searchChosen, setSearchChosen] = useState("");
  const [markedAvailable, setMarkedAvailable] = useState<string[]>([]);
  const [markedChosen, setMarkedChosen] = useState<string[]>([]);

  const byValue = useMemo(() => new Map(options.map((o) => [o.value, o])), [options]);
  const chosen = useMemo(() => [...new Set(value)], [value]);
  const taken = useMemo(() => new Set(chosen), [chosen]);
  // a chosen value the options do not (yet) know, for instance while a list read from a service loads, is shown as it is, never dropped
  const labelOf = (v: string) => byValue.get(v)?.label || byValue.get(v)?.value || v;
  const matches = (v: string, q: string) => !q.trim() || fold(labelOf(v)).includes(fold(q)) || fold(v).includes(fold(q));

  const available = options.filter((o) => !taken.has(o.value) && matches(o.value, searchAvailable));
  const chosenShown = chosen.filter((v) => matches(v, searchChosen));
  const room = max === undefined ? Infinity : Math.max(0, max - chosen.length);
  const filtering = searchChosen.trim() !== "";

  const add = (values: string[]) => {
    const ok = values.filter((v) => byValue.get(v)?.disabled !== true && !taken.has(v)).slice(0, room);
    if (ok.length) onChange([...chosen, ...ok]);
    setMarkedAvailable([]);
  };
  const remove = (values: string[]) => {
    const gone = new Set(values);
    onChange(chosen.filter((v) => !gone.has(v)));
    setMarkedChosen([]);
  };
  const step = (dir: -1 | 1) => {
    const next = [...chosen], on = new Set(markedChosen);
    if (dir === -1) { for (let i = 1; i < next.length; i++) if (on.has(next[i] as string) && !on.has(next[i - 1] as string)) [next[i - 1], next[i]] = [next[i] as string, next[i - 1] as string]; }
    else { for (let i = next.length - 2; i >= 0; i--) if (on.has(next[i] as string) && !on.has(next[i + 1] as string)) [next[i], next[i + 1]] = [next[i + 1] as string, next[i] as string]; }
    onChange(next);
  };
  const edge = (to: "top" | "bottom") => {
    const moved = chosen.filter((v) => markedChosen.includes(v)), rest = chosen.filter((v) => !markedChosen.includes(v));
    onChange(to === "top" ? [...moved, ...rest] : [...rest, ...moved]);
  };
  const canOrder = !disabled && !filtering && markedChosen.some((v) => taken.has(v));
  const hasMarked = markedChosen.some((v) => taken.has(v));

  // consecutive options of the same group are shown under one heading
  const segments: { group: string; items: DualOption[] }[] = [];
  for (const o of available) {
    const group = o.group ?? "";
    const tail = segments[segments.length - 1];
    if (tail && tail.group === group) tail.items.push(o); else segments.push({ group, items: [o] });
  }
  const optionOf = (o: DualOption) => <option key={o.value} value={o.value} disabled={o.disabled}>{o.label || o.value}</option>;

  const dbl = (e: MouseEvent<HTMLSelectElement>, side: "available" | "chosen") => {
    const t = e.target as HTMLElement;
    const v = t.tagName === "OPTION" ? (t as HTMLOptionElement).value : undefined;
    if (v === undefined) return;
    if (side === "available") add([v]); else remove([v]);
  };
  const keys = (e: KeyboardEvent<HTMLSelectElement>, side: "available" | "chosen") => {
    if (disabled) return;
    if (e.key === "Enter") { e.preventDefault(); if (side === "available") add(markedAvailable); else remove(markedChosen); }
    else if (side === "chosen" && e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown") && canOrder) { e.preventDefault(); step(e.key === "ArrowUp" ? -1 : 1); }
  };

  return (
    <div className={`fb-dual${invalid ? " invalid" : ""}`} role="group" aria-labelledby={labelledBy} aria-describedby={describedBy}>
      <div className="fb-dual-pane">
        <h4>{leftTitle} ({options.length - [...taken].filter((v) => byValue.has(v)).length})</h4>
        <input id={id} type="search" aria-label={`Rechercher dans « ${leftTitle} »`} placeholder="Rechercher…" value={searchAvailable} disabled={disabled} onChange={(e) => setSearchAvailable(e.target.value)} />
        <select multiple size={10} aria-label={leftTitle} disabled={disabled} value={markedAvailable} onChange={(e) => setMarkedAvailable(picked(e))} onDoubleClick={(e) => dbl(e, "available")} onKeyDown={(e) => keys(e, "available")}>
          {segments.map((s) => (s.group ? <optgroup key={s.group + s.items[0]?.value} label={s.group}>{s.items.map(optionOf)}</optgroup> : s.items.map(optionOf)))}
        </select>
      </div>
      <div className="fb-dual-actions" role="group" aria-label="Déplacer">
        <button type="button" aria-label="Ajouter la sélection" title="Ajouter la sélection" disabled={disabled || room === 0 || !markedAvailable.length} onClick={() => add(markedAvailable)}>›</button>
        <button type="button" aria-label="Tout ajouter" title={max !== undefined ? `Tout ajouter (au plus ${max})` : "Tout ajouter"} disabled={disabled || room === 0 || !available.some((o) => !o.disabled)} onClick={() => add(available.map((o) => o.value))}>»</button>
        <button type="button" aria-label="Retirer la sélection" title="Retirer la sélection" disabled={disabled || !hasMarked} onClick={() => remove(markedChosen)}>‹</button>
        <button type="button" aria-label="Tout retirer" title="Tout retirer" disabled={disabled || chosenShown.length === 0} onClick={() => remove(chosenShown)}>«</button>
      </div>
      <div className="fb-dual-pane">
        <h4>{rightTitle} ({chosen.length})</h4>
        <input type="search" aria-label={`Rechercher dans « ${rightTitle} »`} placeholder="Rechercher…" value={searchChosen} disabled={disabled} onChange={(e) => setSearchChosen(e.target.value)} />
        <select multiple size={10} aria-label={rightTitle} disabled={disabled} value={markedChosen} onChange={(e) => setMarkedChosen(picked(e))} onDoubleClick={(e) => dbl(e, "chosen")} onKeyDown={(e) => keys(e, "chosen")}>
          {chosenShown.map((v) => <option key={v} value={v}>{labelOf(v)}</option>)}
        </select>
        <div className="fb-dual-order" role="group" aria-label="Mettre en ordre">
          <button type="button" aria-label="Tout en haut" title="Tout en haut" disabled={!canOrder} onClick={() => edge("top")}>⤒</button>
          <button type="button" aria-label="Monter" title="Monter (Alt + ↑)" disabled={!canOrder} onClick={() => step(-1)}>↑</button>
          <button type="button" aria-label="Descendre" title="Descendre (Alt + ↓)" disabled={!canOrder} onClick={() => step(1)}>↓</button>
          <button type="button" aria-label="Tout en bas" title="Tout en bas" disabled={!canOrder} onClick={() => edge("bottom")}>⤓</button>
        </div>
        {filtering && <p className="hint">Videz la recherche pour remettre en ordre.</p>}
      </div>
      <p className="hint fb-dual-count" role="status" aria-live="polite">{chosen.length} sélectionné(s) sur {Math.max(options.length, chosen.length)}{max !== undefined ? ` · au plus ${max}` : ""}</p>
    </div>
  );
}
