import { useId, useRef, useState } from "react";
import { FormIcon } from "./FormIcon";
import type { ControlItem } from "./controls";

interface Props {
  id: string;
  items: ControlItem[];
  /** the values ticked */
  selected: string[];
  multiple: boolean;
  placeholder?: string;
  disabled?: boolean;
  invalid?: boolean;
  max?: number;
  onToggle: (value: string, on: boolean) => void;
}

/** A drop-down with a search box, options as checkboxes (or radio buttons for a single choice) under group headings, some options
 * greyed. The list is plain checkboxes after the search box, so the keyboard and screen readers use it like any group of them. */
export function OptionPicker({ id, items, selected, multiple, placeholder, disabled, invalid, max, onToggle }: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const box = useRef<HTMLDivElement>(null);
  const list = useId();
  const needle = q.trim().toLowerCase();
  const shown = items.filter((o) => !needle || `${o.label ?? ""} ${o.value} ${o.group ?? ""}`.toLowerCase().includes(needle));
  const label = (v: string) => items.find((o) => o.value === v)?.label || v;
  const full = multiple && max !== undefined && selected.length >= max;
  return (
    <div className="opk" ref={box} onBlur={(e) => { if (!box.current?.contains(e.relatedTarget as Node | null)) setOpen(false); }} onKeyDown={(e) => { if (e.key === "Escape" && open) { e.stopPropagation(); setOpen(false); } }}>
      <div className="opk-field">
        <input id={id} role="combobox" aria-expanded={open} aria-controls={list} aria-autocomplete="list" autoComplete="off" disabled={disabled} aria-invalid={invalid ? true : undefined}
          placeholder={placeholder || "Rechercher…"} value={q} onFocus={() => setOpen(true)} onClick={() => setOpen(true)} onChange={(e) => { setQ(e.target.value); setOpen(true); }} />
        <FormIcon name={open ? "up" : "down"} size={14} />
      </div>
      {selected.length > 0 && (
        <ul className="opk-chips" aria-label="Sélection">
          {selected.map((v) => <li key={v}>{label(v)}</li>)}
        </ul>
      )}
      {open && (
        <div id={list} className="opk-pop" role="group" aria-label="Options" onMouseDown={(e) => e.preventDefault()}>
          {shown.length === 0 && <p className="hint">Aucune option ne correspond.</p>}
          {shown.map((o, i) => {
            const on = selected.includes(o.value);
            const head = o.group && o.group !== shown[i - 1]?.group;
            return (
              <div key={o.value}>
                {head && <p className="opk-group">{o.group}</p>}
                <label className={`opk-opt${o.disabled || (!on && full) ? " off" : ""}${o.group ? " in" : ""}`}>
                  <input type={multiple ? "checkbox" : "radio"} name={id} disabled={disabled || o.disabled || (multiple && !on && full)} checked={on}
                    onChange={(e) => { onToggle(o.value, multiple ? e.target.checked : true); if (!multiple) { setOpen(false); setQ(""); } }} /> {o.label || o.value}
                </label>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
