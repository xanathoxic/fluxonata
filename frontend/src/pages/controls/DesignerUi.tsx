import type { ReactNode } from "react";
import { HelpTip } from "../../components/HelpTip";
import { FormIcon, type FormIconName } from "../../forms/FormIcon";

/** The building blocks shared by the designers of a control (cards, panels, switches). */
export const num = (v: string): number | undefined => (v === "" ? undefined : Math.max(0, Math.floor(Number(v))));

/** A choice among a few cards (native radio buttons: arrows, Tab and screen readers behave as for any radio group). */
export function ChoiceCards<T extends string>({ name, label, value, options, onChange, columns = 2, disabled }: { name: string; label: string; value: T; options: { id: T; icon?: FormIconName; title: string; hint: string }[]; onChange: (v: T) => void; columns?: 2 | 3 | 4; disabled?: boolean }) {
  return (
    <div className={`cd-cards cols-${columns}`} role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <label key={o.id} className={`cd-card${value === o.id ? " on" : ""}${disabled ? " off" : ""}`}>
          <input type="radio" name={name} value={o.id} checked={value === o.id} disabled={disabled} onChange={() => onChange(o.id)} />
          {o.icon && <span className="cd-card-ic"><FormIcon name={o.icon} size={22} /></span>}
          <span className="cd-card-tx"><span className="cd-card-t">{o.title}</span><span className="cd-card-h">{o.hint}</span></span>
        </label>
      ))}
    </div>
  );
}

export function Card({ icon, title, help, children }: { icon: FormIconName; title: string; help?: string; children: ReactNode }) {
  return (
    <section className="cd-sec" aria-label={title}>
      <h3><FormIcon name={icon} size={16} /> {title}{help && <HelpTip label={`Aide : ${title}`}>{help}</HelpTip>}</h3>
      {children}
    </section>
  );
}
export function Panel({ icon, title, subtitle, children, fill }: { icon: FormIconName; title: string; subtitle: string; children: ReactNode; fill?: boolean }) {
  return (
    <div className={`cd-panel${fill ? " fill" : ""}`}>
      <header className="cd-ph"><span className="cd-ph-ic"><FormIcon name={icon} size={20} /></span><div><h2>{title}</h2><p>{subtitle}</p></div></header>
      <div className="cd-pb">{children}</div>
    </div>
  );
}
export function Num({ id, label, value, max, placeholder, onChange }: { id: string; label: string; value: number | undefined; max: number; placeholder?: string; onChange: (v: number | undefined) => void }) {
  return <div className="field"><label htmlFor={id}>{label}</label><input id={id} type="number" min={0} max={max} value={value ?? ""} placeholder={placeholder} onChange={(e) => onChange(num(e.target.value))} /></div>;
}
export function Switch({ id, checked, onChange, label, help }: { id: string; checked: boolean; onChange: (v: boolean) => void; label: string; help?: string }) {
  return <div className="cd-switch"><input id={id} type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /><label htmlFor={id}>{label}</label>{help && <HelpTip label={`Aide : ${label}`}>{help}</HelpTip>}</div>;
}

