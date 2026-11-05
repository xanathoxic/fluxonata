import { useState, type ReactNode } from "react";
import { FormIcon, type FormIconName } from "../FormIcon";
import { UNITS, formatBox, formatColor, formatShadows, joinLength, parseBox, parseColor, parseGradient, formatGradient, parseShadows, splitLength, toHex, type Box, type Shadow } from "./values";

/** A labelled control with a small × that removes the declaration. */
export function Row({ label, set, onReset, children, wide }: { label: string; set: boolean; onReset: () => void; children: ReactNode; wide?: boolean }) {
  return (
    <div className={`cs-row${wide ? " wide" : ""}`}>
      <span className="cs-label">{label}</span>
      <div className="cs-ctl">{children}</div>
      {set ? <button type="button" className="cs-x" aria-label={`Retirer : ${label}`} title="Retirer ce réglage" onClick={onReset}><FormIcon name="close" size={14} /></button> : <span className="cs-x" aria-hidden="true" />}
    </div>
  );
}

/** A number with a unit, and a slider when the range is known. Any value that is not a plain length (auto, calc(), var()) stays editable as text. */
export function Length({ value, onChange, units = ["px", "rem", "em", "%"], min = 0, max = 100, step = 1, slider, label, placeholder }: { value: string; onChange: (v: string) => void; units?: readonly string[]; min?: number; max?: number; step?: number; slider?: boolean; label: string; placeholder?: string }) {
  const parts = value ? splitLength(value) : { n: "", unit: units[0] as string };
  if (value && !parts) return <input className="cs-text" aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} />;
  const p = parts as { n: string; unit: string };
  const unit = p.unit || (units[0] as string);
  return (
    <span className="cs-len">
      <input type="number" aria-label={label} value={p.n} min={min} max={max} step={step} placeholder={placeholder ?? "auto"} onChange={(e) => onChange(joinLength(e.target.value, unit))} />
      <select aria-label={`${label} : unité`} value={unit} onChange={(e) => onChange(p.n === "" ? "" : joinLength(p.n, e.target.value))}>{units.map((u) => <option key={u} value={u}>{u}</option>)}</select>
      {slider && <input type="range" aria-label={`${label} : curseur`} min={min} max={max} step={step} value={p.n === "" ? min : Number(p.n)} onChange={(e) => onChange(joinLength(e.target.value, unit))} />}
    </span>
  );
}

const THEME_COLORS: [string, string][] = [["var(--accent)", "Couleur d'accent"], ["var(--fg)", "Texte"], ["var(--muted)", "Texte atténué"], ["var(--border)", "Bordure"], ["var(--surface)", "Surface"], ["var(--bg)", "Fond"], ["transparent", "Transparent"]];
const THEME_DOT: Record<string, string> = { "var(--accent)": "var(--accent)", "var(--fg)": "var(--fg)", "var(--muted)": "var(--muted)", "var(--border)": "var(--border)", "var(--surface)": "var(--surface)", "var(--bg)": "var(--bg)", transparent: "repeating-conic-gradient(#cbd5e1 0 25%, #fff 0 50%) 50% / 8px 8px" };

/** A colour: picker, hex or rgba text, opacity, and the colours of the app's theme. */
export function Color({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  const c = parseColor(value);
  return (
    <span className="cs-color">
      <span className="cs-cline">
        <input type="color" aria-label={`${label} : choisir`} value={c ? toHex(c) : "#000000"} onChange={(e) => onChange(formatColor({ ...(c ?? { r: 0, g: 0, b: 0, a: 1 }), ...hexToRgb(e.target.value) }))} />
        <input className="cs-text" aria-label={`${label} : code`} value={value} placeholder="#rrggbb" onChange={(e) => onChange(e.target.value)} maxLength={40} />
      </span>
      {c && <input type="range" aria-label={`${label} : opacité`} title={`Opacité ${Math.round(c.a * 100)} %`} min={0} max={1} step={0.05} value={c.a} onChange={(e) => onChange(formatColor({ ...c, a: Number(e.target.value) }))} />}
      <span className="cs-chips" role="group" aria-label={`${label} : couleurs du thème`}>
        {THEME_COLORS.map(([v, name]) => <button key={v} type="button" className="cs-chip" style={{ background: THEME_DOT[v] }} aria-label={name} title={name} aria-pressed={value === v} onClick={() => onChange(v)} />)}
      </span>
    </span>
  );
}
const hexToRgb = (hex: string) => { const n = parseInt(hex.slice(1), 16); return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 }; };

export interface Choice { id: string; label: string; icon?: FormIconName; glyph?: string }
/** A short list of options as small icon (or glyph) buttons; choosing the active one again removes the setting. */
export function Choices({ value, onChange, options, label }: { value: string; onChange: (v: string) => void; options: Choice[]; label: string }) {
  return (
    <span className="cs-choices" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button key={o.id} type="button" role="radio" aria-checked={value === o.id} className={`cs-opt${value === o.id ? " on" : ""}`} aria-label={o.label} title={o.label} onClick={() => onChange(value === o.id ? "" : o.id)}>
          {o.icon ? <FormIcon name={o.icon} size={16} /> : <span className="cs-glyph" aria-hidden="true">{o.glyph}</span>}
        </button>
      ))}
    </span>
  );
}

/** Four sides (padding, margin, radius…) with a lock that links them. */
export function Sides({ value, onChange, label, names = ["Haut", "Droite", "Bas", "Gauche"], max = 80 }: { value: string; onChange: (v: string) => void; label: string; names?: string[]; max?: number }) {
  const sides = parseBox(value);
  const unit = splitLength(sides.find((s) => s) ?? "")?.unit || "px";
  const same = sides.every((s) => s === sides[0]);
  const [linked, setLinked] = useState(same);
  const edit = (i: number, v: string) => {
    const n: Box = linked ? [v, v, v, v] : (sides.map((s, k) => (k === i ? v : s)) as Box);
    onChange(formatBox(n.map((x) => (x === "" ? "" : x)) as Box));
  };
  const num = (s: string) => splitLength(s)?.n ?? "";
  return (
    <span className="cs-sides">
      <button type="button" className={`cs-opt${linked ? " on" : ""}`} aria-pressed={linked} aria-label={`${label} : lier les quatre côtés`} title={linked ? "Quatre côtés liés" : "Côtés indépendants"} onClick={() => setLinked(!linked)}><FormIcon name={linked ? "lock" : "unlock"} size={16} /></button>
      {(linked ? [0] : [0, 1, 2, 3]).map((i) => (
        <input key={i} type="number" min={0} max={max} aria-label={linked ? label : `${label} : ${names[i]?.toLowerCase()}`} title={linked ? label : names[i]} placeholder={linked ? "tous" : (names[i] ?? "").slice(0, 1)} value={num(sides[i] ?? "")} onChange={(e) => edit(i, joinLength(e.target.value, unit))} />
      ))}
      <select aria-label={`${label} : unité`} value={unit} onChange={(e) => onChange(formatBox(sides.map((s) => (s ? joinLength(num(s), e.target.value) : "")) as Box))}>{UNITS.slice(0, 3).map((u) => <option key={u} value={u}>{u}</option>)}</select>
    </span>
  );
}

const blank = (): Shadow => ({ inset: false, x: 0, y: 2, blur: 8, spread: 0, color: "rgba(0, 0, 0, 0.2)" });
/** Up to three shadow layers, each with offsets, blur, spread, colour and an inset switch. */
export function Shadows({ value, onChange, label, text }: { value: string; onChange: (v: string) => void; label: string; text?: boolean }) {
  const layers = parseShadows(value);
  if (layers === null) return <input className="cs-text" aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} />;
  const set = (next: Shadow[]) => onChange(text ? next.map((l) => `${l.x}px ${l.y}px ${l.blur}px ${l.color}`).join(", ") : formatShadows(next));
  const upd = (i: number, patch: Partial<Shadow>) => set(layers.map((l, k) => (k === i ? { ...l, ...patch } : l)));
  return (
    <span className="cs-shadows">
      {layers.map((l, i) => (
        <span key={i} className="cs-layer" role="group" aria-label={`${label} ${i + 1}`}>
          {(text ? (["x", "y", "blur"] as const) : (["x", "y", "blur", "spread"] as const)).map((k) => <input key={k} type="number" aria-label={`${label} ${i + 1} : ${k === "x" ? "décalage horizontal" : k === "y" ? "décalage vertical" : k === "blur" ? "flou" : "étendue"}`} title={k === "x" ? "Horizontal" : k === "y" ? "Vertical" : k === "blur" ? "Flou" : "Étendue"} placeholder={k === "x" ? "x" : k === "y" ? "y" : k === "blur" ? "flou" : "ét."} value={l[k]} onChange={(e) => upd(i, { [k]: Number(e.target.value) } as Partial<Shadow>)} />)}
          {!text && <button type="button" className={`cs-opt${l.inset ? " on" : ""}`} aria-pressed={l.inset} aria-label={`${label} ${i + 1} : intérieure`} title="Ombre intérieure" onClick={() => upd(i, { inset: !l.inset })}><FormIcon name="border" size={14} /></button>}
          <button type="button" className="cs-opt" aria-label={`${label} ${i + 1} : supprimer`} title="Supprimer cette couche" onClick={() => set(layers.filter((_, k) => k !== i))}><FormIcon name="close" size={14} /></button>
          <Color value={l.color} label={`${label} ${i + 1} : couleur`} onChange={(color) => upd(i, { color })} />
        </span>
      ))}
      {layers.length < 3 && <button type="button" className="cs-add" onClick={() => set([...layers, blank()])}><FormIcon name="plus" size={14} /> Couche</button>}
    </span>
  );
}

/** A two-colour linear gradient with its angle. */
export function GradientField({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  const g = value ? parseGradient(value) : null;
  if (!value) return <button type="button" className="cs-add" onClick={() => onChange(formatGradient({ angle: 135, from: "#ffffff", to: "#e0f2f7" }))}><FormIcon name="plus" size={14} /> Dégradé</button>;
  if (!g) return <input className="cs-text" aria-label={label} value={value} onChange={(e) => onChange(e.target.value)} />;
  return (
    <span className="cs-grad">
      <input type="range" min={0} max={360} aria-label={`${label} : angle`} title={`Angle ${g.angle}°`} value={g.angle} onChange={(e) => onChange(formatGradient({ ...g, angle: Number(e.target.value) }))} />
      <Color value={g.from} label={`${label} : départ`} onChange={(from) => onChange(formatGradient({ ...g, from }))} />
      <Color value={g.to} label={`${label} : arrivée`} onChange={(to) => onChange(formatGradient({ ...g, to }))} />
    </span>
  );
}
