import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { FormIcon, type FormIconName } from "./FormIcon";
import { ACCENTS, MIN_CONTRAST, contrastWithWhite, isHex } from "./style";
import type { FormStyle } from "./types";

interface Opt<T extends string> { id: T; label: string; icon: FormIconName }

/** A small icon button that opens a context menu below it. Escape or a click elsewhere closes it and focus goes back to the button. */
export function Pop({ label, icon, children, badge }: { label: string; icon: ReactNode; badge?: ReactNode; children: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLSpanElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const [flip, setFlip] = useState(false);
  const id = useId();
  // a menu never leaves the window: opened near the right edge it unfolds towards the left
  useLayoutEffect(() => {
    if (!open) { setFlip(false); return; }
    const box = popup.current?.getBoundingClientRect();
    if (box && box.right > window.innerWidth - 8) setFlip(true);
  }, [open]);
  const close = () => { setOpen(false); root.current?.querySelector<HTMLButtonElement>(".fb-sbtn")?.focus(); };
  useEffect(() => {
    if (!open) return;
    const down = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    // Escape closes the menu even when the focus was lost (after deleting an item, for instance)
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { setOpen(false); root.current?.querySelector<HTMLButtonElement>(".fb-sbtn")?.focus(); } };
    document.addEventListener("pointerdown", down);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("pointerdown", down); document.removeEventListener("keydown", esc); };
  }, [open]);
  useEffect(() => { // the menu opens on its checked (or first) item
    if (open) root.current?.querySelector<HTMLElement>('[aria-checked="true"], .fb-pop button, .fb-pop input')?.focus();
  }, [open]);
  const keys = (e: React.KeyboardEvent) => {
    const items = [...(root.current?.querySelectorAll<HTMLElement>('[role="menuitemradio"]') ?? [])];
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (items.length === 0 || at < 0) return;
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (step) { e.preventDefault(); items[(at + step + items.length) % items.length]?.focus(); }
  };
  return (
    <span className="fb-popwrap" ref={root} onKeyDown={keys}>
      <button type="button" className="fb-sbtn" aria-label={label} title={label} aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen(!open)}>{icon}{badge}</button>
      {open && <div id={id} ref={popup} className={`fb-pop${flip ? " flip" : ""}`} role="group" aria-label={label}>{children(close)}</div>}
    </span>
  );
}

/** One closed choice: the button shows the icon of the current option, the menu lists the options as icons only (name and help in the tooltip). */
function Choice<T extends string>({ legend, value, options, onChange, disabled }: { legend: string; value: T; options: Opt<T>[]; onChange: (v: T) => void; disabled?: boolean }) {
  const current = options.find((o) => o.id === value) ?? (options[0] as Opt<T>);
  if (disabled) return <span className="fb-sbtn" role="img" aria-label={`${legend} : ${current.label}`} title={`${legend} : ${current.label}`}><FormIcon name={current.icon} /></span>;
  return (
    <Pop label={`${legend} : ${current.label}`} icon={<FormIcon name={current.icon} />}>
      {(close) => (
        <div role="menu" aria-label={legend} className="fb-popmenu">
          {options.map((o) => (
            <button key={o.id} type="button" role="menuitemradio" aria-checked={value === o.id} className={`fb-sopt${value === o.id ? " on" : ""}`} aria-label={o.label} title={o.label} onClick={() => { onChange(o.id); close(); }}>
              <FormIcon name={o.icon} />
            </button>
          ))}
        </div>
      )}
    </Pop>
  );
}

/** How the form looks: a horizontal bar of small icons, each opening a menu of options. The preview below is the real form. */
export function StyleEditor({ style, onChange, edit }: { style: FormStyle | undefined; onChange: (s: FormStyle | undefined) => void; edit: boolean }) {
  const s = style ?? {};
  const set = (patch: Partial<FormStyle>) => { const next = { ...s, ...patch }; onChange(Object.keys(next).length ? next : undefined); };
  const [hex, setHex] = useState(s.accent ?? "");
  const [problem, setProblem] = useState("");
  useEffect(() => { setHex(s.accent ?? ""); }, [s.accent]);
  const accent = (v: string) => {
    setHex(v);
    if (!isHex(v)) {
      setProblem(v === "" ? "" : "Format attendu : #rrggbb.");
      if (v === "") { const { accent: _a, ...rest } = s; onChange(Object.keys(rest).length ? rest : undefined); }
      return;
    }
    if (contrastWithWhite(v) < MIN_CONTRAST) { setProblem("Couleur trop claire : le texte blanc des boutons ne serait pas lisible. Choisissez une teinte plus foncée."); return; }
    setProblem("");
    set({ accent: v.toLowerCase() });
  };
  const dot = <span className="fb-dot" style={{ background: s.accent ?? "var(--accent)" }} aria-hidden="true" />;
  return (
    <div className="fb-stylebar" role="toolbar" aria-label="Style du formulaire">
      {edit ? (
        <Pop label={`Couleur d'accent : ${s.accent ?? "par défaut"}`} icon={<FormIcon name="palette" />} badge={dot}>
          {() => (
            <div className="fb-accentpop">
              <div className="fb-swatches" role="group" aria-label="Couleurs proposées">
                {ACCENTS.map((c) => <button key={c} type="button" className="fb-swatch" style={{ background: c }} aria-pressed={s.accent === c} aria-label={`Couleur ${c}`} title={c} onClick={() => accent(c)} />)}
              </div>
              <div className="fb-swatches">
                <input id="st-color" type="color" aria-label="Couleur personnalisée" title="Couleur personnalisée" value={isHex(hex) ? hex : "#0e7490"} onChange={(e) => accent(e.target.value)} />
                <input id="st-hex" aria-label="Code couleur" title="Code couleur (#rrggbb)" value={hex} onChange={(e) => accent(e.target.value)} placeholder="#0e7490" maxLength={7} aria-describedby={problem ? "st-hex-err" : undefined} />
              </div>
              {problem && <p id="st-hex-err" role="alert" className="field-error">{problem}</p>}
            </div>
          )}
        </Pop>
      ) : <span className="fb-sbtn" role="img" aria-label="Couleur d'accent" title="Couleur d'accent"><FormIcon name="palette" />{dot}</span>}
      <span className="fb-sep" aria-hidden="true" />
      <Choice legend="Densité" value={s.density ?? "normal"} disabled={!edit} onChange={(density) => set({ density })} options={[{ id: "compact", label: "Compacte", icon: "dense1" }, { id: "normal", label: "Normale", icon: "dense2" }, { id: "comfortable", label: "Aérée", icon: "dense3" }]} />
      <Choice legend="Libellés" value={s.labels ?? "top"} disabled={!edit} onChange={(labels) => set({ labels })} options={[{ id: "top", label: "Au-dessus", icon: "labelTop" }, { id: "left", label: "À gauche", icon: "labelLeft" }]} />
      <Choice legend="Coins" value={s.radius ?? "medium"} disabled={!edit} onChange={(radius) => set({ radius })} options={[{ id: "none", label: "Droits", icon: "corner0" }, { id: "small", label: "Légers", icon: "corner1" }, { id: "medium", label: "Arrondis", icon: "corner2" }, { id: "large", label: "Très arrondis", icon: "corner3" }]} />
      <Choice legend="Largeur du formulaire" value={s.width ?? "medium"} disabled={!edit} onChange={(width) => set({ width })} options={[{ id: "narrow", label: "Étroit", icon: "width1" }, { id: "medium", label: "Moyen", icon: "width2" }, { id: "wide", label: "Large", icon: "width3" }, { id: "full", label: "Pleine largeur", icon: "width4" }]} />
      <Choice legend="Aspect des sections" value={s.sections ?? "card"} disabled={!edit} onChange={(sections) => set({ sections })} options={[{ id: "card", label: "Cartes", icon: "secCard" }, { id: "plain", label: "Simples", icon: "secPlain" }, { id: "line", label: "Filets", icon: "secLine" }]} />
      <Choice legend="Bouton d'envoi" value={s.button ?? "left"} disabled={!edit} onChange={(button) => set({ button })} options={[{ id: "left", label: "À gauche", icon: "btnLeft" }, { id: "center", label: "Centré", icon: "btnCenter" }, { id: "right", label: "À droite", icon: "btnRight" }, { id: "full", label: "Pleine largeur du bouton", icon: "btnFull" }]} />
      {edit && <><span className="fb-sep" aria-hidden="true" /><button type="button" className="fb-sbtn" aria-label="Réinitialiser le style" title="Réinitialiser le style" onClick={() => { setHex(""); setProblem(""); onChange(undefined); }} disabled={!style}><FormIcon name="undo" /></button></>}
    </div>
  );
}
