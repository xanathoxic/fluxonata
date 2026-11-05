import { useState } from "react";
import { FormIcon, type FormIconName } from "../FormIcon";
import { Choices, Color, GradientField, Length, Row, Shadows, Sides } from "./controls";
import { getDecl, type Rule } from "./model";
import { FONT_STACKS, formatBorder, parseBorder, type Border } from "./values";

/** Properties the server accepts (triggers/formcss.py): the « other declaration » menu offers exactly these. */
export const ALLOWED = ["accent-color", "align-content", "align-items", "align-self", "aspect-ratio", "background", "background-color", "background-image", "background-position", "background-repeat", "background-size", "border", "border-bottom", "border-bottom-color", "border-bottom-left-radius", "border-bottom-right-radius", "border-bottom-style", "border-bottom-width", "border-color", "border-left", "border-left-color", "border-left-style", "border-left-width", "border-radius", "border-right", "border-right-color", "border-right-style", "border-right-width", "border-style", "border-top", "border-top-color", "border-top-left-radius", "border-top-right-radius", "border-top-style", "border-top-width", "border-width", "box-shadow", "box-sizing", "color", "column-gap", "content", "cursor", "display", "flex", "flex-basis", "flex-direction", "flex-grow", "flex-shrink", "flex-wrap", "font", "font-family", "font-size", "font-style", "font-weight", "gap", "grid-template-columns", "height", "justify-content", "justify-items", "justify-self", "letter-spacing", "line-height", "margin", "margin-bottom", "margin-left", "margin-right", "margin-top", "max-height", "max-width", "min-height", "min-width", "opacity", "outline", "outline-color", "outline-offset", "outline-style", "outline-width", "overflow", "padding", "padding-bottom", "padding-left", "padding-right", "padding-top", "position", "row-gap", "text-align", "text-decoration", "text-decoration-color", "text-indent", "text-shadow", "text-transform", "text-underline-offset", "transform", "transition", "transition-delay", "transition-duration", "transition-property", "transition-timing-function", "white-space", "width", "word-spacing"];

interface Section { id: string; label: string; icon: FormIconName; props: RegExp }
export const SECTIONS: Section[] = [
  { id: "type", label: "Typographie", icon: "type", props: /^(font|line-height|letter-spacing|word-spacing|text-align|text-transform|text-decoration|text-indent|text-underline|white-space)/ },
  { id: "color", label: "Couleurs et fond", icon: "fill", props: /^(color|background|accent-color|opacity)/ },
  { id: "border", label: "Bordure et coins", icon: "border", props: /^(border|outline)/ },
  { id: "spacing", label: "Espacement", icon: "spacing", props: /^(padding|margin|gap|row-gap|column-gap)/ },
  { id: "size", label: "Taille", icon: "size", props: /^(width|height|min-|max-|aspect-ratio|box-sizing)/ },
  { id: "shadow", label: "Ombres", icon: "shadow", props: /^(box-shadow|text-shadow)$/ },
  { id: "layout", label: "Disposition", icon: "layout", props: /^(display|flex|align|justify|grid|overflow|position)/ },
  { id: "effects", label: "Effets et animation", icon: "effects", props: /^(transition|transform|cursor|content)/ },
  { id: "other", label: "Autres déclarations", icon: "sliders", props: /^$/ },
];

interface Props { rule: Rule | undefined; set: (prop: string, value: string) => void; setMany: (c: Record<string, string>) => void; disabled?: boolean }

const WEIGHTS = ["300", "400", "500", "600", "700", "800"];
const sideProp = (side: string) => (side === "all" ? "border" : `border-${side}`);
const SIDES = ["top", "right", "bottom", "left"];

function Typography({ rule, set }: Props) {
  const g = (p: string) => getDecl(rule, p);
  const stack = FONT_STACKS.find((f) => f.css === g("font-family"));
  return (
    <>
      <Row label="Police" set={!!g("font-family")} onReset={() => set("font-family", "")}>
        <select aria-label="Police" value={stack?.css ?? g("font-family")} onChange={(e) => set("font-family", e.target.value)}>
          <option value="">Par défaut</option>
          {FONT_STACKS.map((f) => <option key={f.id} value={f.css}>{f.label}</option>)}
          {g("font-family") && !stack && <option value={g("font-family")}>Personnalisée</option>}
        </select>
      </Row>
      <Row label="Taille" set={!!g("font-size")} onReset={() => set("font-size", "")}><Length label="Taille du texte" value={g("font-size")} onChange={(v) => set("font-size", v)} units={["px", "rem", "em"]} min={8} max={64} step={1} slider /></Row>
      <Row label="Graisse" set={!!g("font-weight")} onReset={() => set("font-weight", "")}>
        <select aria-label="Graisse" value={g("font-weight")} onChange={(e) => set("font-weight", e.target.value)}><option value="">Par défaut</option>{WEIGHTS.map((w) => <option key={w} value={w}>{w}{w === "400" ? " (normal)" : w === "700" ? " (gras)" : ""}</option>)}</select>
      </Row>
      <Row label="Style" set={!!g("font-style") || !!g("text-decoration")} onReset={() => { set("font-style", ""); set("text-decoration", ""); }}>
        <span className="cs-group">
          <Choices label="Italique" value={g("font-style")} onChange={(v) => set("font-style", v)} options={[{ id: "italic", label: "Italique", glyph: "I" }]} />
          <Choices label="Décoration" value={g("text-decoration")} onChange={(v) => set("text-decoration", v)} options={[{ id: "underline", label: "Souligné", glyph: "U" }, { id: "line-through", label: "Barré", glyph: "S" }]} />
        </span>
      </Row>
      <Row label="Casse" set={!!g("text-transform")} onReset={() => set("text-transform", "")}>
        <Choices label="Casse" value={g("text-transform")} onChange={(v) => set("text-transform", v)} options={[{ id: "none", label: "Normale", glyph: "Aa" }, { id: "uppercase", label: "MAJUSCULES", glyph: "AA" }, { id: "lowercase", label: "minuscules", glyph: "aa" }, { id: "capitalize", label: "Initiales en majuscule", glyph: "Ab" }]} />
      </Row>
      <Row label="Alignement" set={!!g("text-align")} onReset={() => set("text-align", "")}>
        <Choices label="Alignement du texte" value={g("text-align")} onChange={(v) => set("text-align", v)} options={[{ id: "left", label: "À gauche", icon: "alignL" }, { id: "center", label: "Centré", icon: "alignC" }, { id: "right", label: "À droite", icon: "alignR" }, { id: "justify", label: "Justifié", icon: "alignJ" }]} />
      </Row>
      <Row label="Interligne" set={!!g("line-height")} onReset={() => set("line-height", "")}>
        <input className="cs-num" type="number" step={0.05} min={0.8} max={3} aria-label="Interligne" placeholder="1.5" value={g("line-height")} onChange={(e) => set("line-height", e.target.value)} />
      </Row>
      <Row label="Espacement des lettres" set={!!g("letter-spacing")} onReset={() => set("letter-spacing", "")}><Length label="Espacement des lettres" value={g("letter-spacing")} onChange={(v) => set("letter-spacing", v)} units={["em", "px"]} min={-0.1} max={1} step={0.01} /></Row>
    </>
  );
}

function Colors({ rule, set }: Props) {
  const g = (p: string) => getDecl(rule, p);
  return (
    <>
      <Row label="Texte" set={!!g("color")} onReset={() => set("color", "")} wide><Color label="Couleur du texte" value={g("color")} onChange={(v) => set("color", v)} /></Row>
      <Row label="Fond" set={!!g("background-color")} onReset={() => set("background-color", "")} wide><Color label="Couleur de fond" value={g("background-color")} onChange={(v) => set("background-color", v)} /></Row>
      <Row label="Dégradé" set={!!g("background-image")} onReset={() => set("background-image", "")} wide><GradientField label="Dégradé de fond" value={g("background-image")} onChange={(v) => set("background-image", v)} /></Row>
      <Row label="Contrôles" set={!!g("accent-color")} onReset={() => set("accent-color", "")} wide><Color label="Couleur des cases et boutons radio" value={g("accent-color")} onChange={(v) => set("accent-color", v)} /></Row>
      <Row label="Opacité" set={!!g("opacity")} onReset={() => set("opacity", "")}>
        <span className="cs-len"><input type="range" aria-label="Opacité" min={0} max={1} step={0.05} value={g("opacity") === "" ? 1 : Number(g("opacity"))} onChange={(e) => set("opacity", e.target.value === "1" ? "" : e.target.value)} /><output>{g("opacity") === "" ? "100" : Math.round(Number(g("opacity")) * 100)} %</output></span>
      </Row>
    </>
  );
}

function Borders({ rule, set, setMany }: Props) {
  const [side, setSide] = useState("all");
  const g = (p: string) => getDecl(rule, p);
  const prop = sideProp(side);
  const current: Border = parseBorder(g(prop) || (side !== "all" ? g("border") : ""));
  const write = (patch: Partial<Border>) => {
    const next = formatBorder({ ...current, ...patch });
    if (side !== "all") { set(prop, next); return; }
    // « all » is written before the sides that override it
    const sides = Object.fromEntries(SIDES.filter((s) => g(`border-${s}`)).map((s) => [`border-${s}`, g(`border-${s}`)]));
    setMany({ ...Object.fromEntries(Object.keys(sides).map((k) => [k, ""])), border: next, ...sides });
  };
  const radiusNames = ["Haut gauche", "Haut droite", "Bas droite", "Bas gauche"];
  return (
    <>
      <Row label="Côté" set={false} onReset={() => undefined}>
        <Choices label="Côté de la bordure" value={side} onChange={(v) => setSide(v || "all")} options={[{ id: "all", label: "Tous les côtés", glyph: "▢" }, { id: "top", label: "Haut", glyph: "▔" }, { id: "right", label: "Droite", glyph: "▕" }, { id: "bottom", label: "Bas", glyph: "▁" }, { id: "left", label: "Gauche", glyph: "▏" }]} />
      </Row>
      <Row label="Épaisseur" set={!!g(prop)} onReset={() => set(prop, "")}><Length label="Épaisseur de la bordure" value={current.w} onChange={(v) => write({ w: v })} units={["px"]} min={0} max={12} slider placeholder="0" /></Row>
      <Row label="Trait" set={!!current.s} onReset={() => write({ s: "none" })}><Choices label="Style du trait" value={current.s} onChange={(v) => write({ s: v || "solid" })} options={[{ id: "solid", label: "Plein", glyph: "—" }, { id: "dashed", label: "Tirets", glyph: "- -" }, { id: "dotted", label: "Pointillés", glyph: "···" }, { id: "none", label: "Aucun", glyph: "∅" }]} /></Row>
      <Row label="Couleur" set={!!current.c} onReset={() => write({ c: "" })} wide><Color label="Couleur de la bordure" value={current.c} onChange={(v) => write({ c: v })} /></Row>
      <Row label="Coins" set={!!g("border-radius")} onReset={() => set("border-radius", "")}><Sides label="Rayon des coins" value={g("border-radius")} onChange={(v) => set("border-radius", v)} names={radiusNames} max={60} /></Row>
      <Row label="Contour" set={!!g("outline")} onReset={() => { set("outline", ""); set("outline-offset", ""); }} wide>
        <Color label="Couleur du contour" value={parseBorder(g("outline")).c} onChange={(v) => set("outline", formatBorder({ ...parseBorder(g("outline")), w: parseBorder(g("outline")).w || "2px", c: v }))} />
        <Length label="Épaisseur du contour" value={parseBorder(g("outline")).w} onChange={(v) => set("outline", formatBorder({ ...parseBorder(g("outline")), w: v }))} units={["px"]} min={0} max={8} placeholder="2" />
        <Length label="Décalage du contour" value={g("outline-offset")} onChange={(v) => set("outline-offset", v)} units={["px"]} min={-8} max={16} placeholder="0" />
      </Row>
    </>
  );
}

function Spacing({ rule, set }: Props) {
  const g = (p: string) => getDecl(rule, p);
  return (
    <>
      <Row label="Intérieur" set={!!g("padding")} onReset={() => set("padding", "")}><Sides label="Marge intérieure" value={g("padding")} onChange={(v) => set("padding", v)} /></Row>
      <Row label="Extérieur" set={!!g("margin")} onReset={() => set("margin", "")}><Sides label="Marge extérieure" value={g("margin")} onChange={(v) => set("margin", v)} /></Row>
      <Row label="Entre éléments" set={!!g("gap")} onReset={() => set("gap", "")}><Length label="Espace entre éléments" value={g("gap")} onChange={(v) => set("gap", v)} units={["px", "rem", "em"]} min={0} max={80} slider /></Row>
    </>
  );
}

function Size({ rule, set }: Props) {
  const g = (p: string) => getDecl(rule, p);
  const one = (label: string, prop: string) => <Row key={prop} label={label} set={!!g(prop)} onReset={() => set(prop, "")}><Length label={label} value={g(prop)} onChange={(v) => set(prop, v)} units={["px", "%", "rem", "em", "vw", "vh"]} min={0} max={2000} /></Row>;
  return <>{one("Largeur", "width")}{one("Largeur min.", "min-width")}{one("Largeur max.", "max-width")}{one("Hauteur", "height")}{one("Hauteur min.", "min-height")}{one("Hauteur max.", "max-height")}</>;
}

function ShadowsPanel({ rule, set }: Props) {
  const g = (p: string) => getDecl(rule, p);
  return (
    <>
      <Row label="Ombre du bloc" set={!!g("box-shadow")} onReset={() => set("box-shadow", "")} wide><Shadows label="Ombre du bloc" value={g("box-shadow")} onChange={(v) => set("box-shadow", v)} /></Row>
      <Row label="Ombre du texte" set={!!g("text-shadow")} onReset={() => set("text-shadow", "")} wide><Shadows text label="Ombre du texte" value={g("text-shadow")} onChange={(v) => set("text-shadow", v)} /></Row>
    </>
  );
}

const J = [{ id: "flex-start", label: "Début", glyph: "⇤" }, { id: "center", label: "Centre", glyph: "↔" }, { id: "flex-end", label: "Fin", glyph: "⇥" }];
function LayoutPanel({ rule, set }: Props) {
  const g = (p: string) => getDecl(rule, p);
  return (
    <>
      <Row label="Affichage" set={!!g("display")} onReset={() => set("display", "")}>
        <Choices label="Affichage" value={g("display")} onChange={(v) => set("display", v)} options={[{ id: "block", label: "Bloc", glyph: "▭" }, { id: "inline-block", label: "En ligne", glyph: "▱" }, { id: "flex", label: "Flex", icon: "layout" }, { id: "grid", label: "Grille", icon: "datagrid" }, { id: "none", label: "Masqué", icon: "eyeOff" }]} />
      </Row>
      <Row label="Direction" set={!!g("flex-direction")} onReset={() => set("flex-direction", "")}><Choices label="Direction" value={g("flex-direction")} onChange={(v) => set("flex-direction", v)} options={[{ id: "row", label: "En ligne", icon: "rowDir" }, { id: "column", label: "En colonne", icon: "colDir" }]} /></Row>
      <Row label="Retour à la ligne" set={!!g("flex-wrap")} onReset={() => set("flex-wrap", "")}><Choices label="Retour à la ligne" value={g("flex-wrap")} onChange={(v) => set("flex-wrap", v)} options={[{ id: "nowrap", label: "Sur une ligne", glyph: "→" }, { id: "wrap", label: "Sur plusieurs lignes", glyph: "↩" }]} /></Row>
      <Row label="Alignement" set={!!g("align-items")} onReset={() => set("align-items", "")}><Choices label="Alignement transversal" value={g("align-items")} onChange={(v) => set("align-items", v)} options={[...J, { id: "stretch", label: "Étiré", glyph: "↕" }]} /></Row>
      <Row label="Répartition" set={!!g("justify-content")} onReset={() => set("justify-content", "")}><Choices label="Répartition" value={g("justify-content")} onChange={(v) => set("justify-content", v)} options={[...J, { id: "space-between", label: "Espacé", glyph: "⇹" }]} /></Row>
      <Row label="Colonnes" set={!!g("grid-template-columns")} onReset={() => set("grid-template-columns", "")}><input className="cs-text" aria-label="Colonnes de la grille" placeholder="1fr 1fr" value={g("grid-template-columns")} onChange={(e) => set("grid-template-columns", e.target.value)} /></Row>
      <Row label="Débordement" set={!!g("overflow")} onReset={() => set("overflow", "")}><Choices label="Débordement" value={g("overflow")} onChange={(v) => set("overflow", v)} options={[{ id: "visible", label: "Visible", glyph: "▢" }, { id: "hidden", label: "Masqué", glyph: "✂" }, { id: "auto", label: "Défilement", glyph: "⇅" }]} /></Row>
    </>
  );
}

function Effects({ rule, set }: Props) {
  const g = (p: string) => getDecl(rule, p);
  const t = g("transform");
  const part = (re: RegExp) => re.exec(t)?.[1] ?? "";
  const setT = (k: "translateY" | "scale" | "rotate", v: string) => {
    const cur: Record<string, string> = { translateY: part(/translateY\(([^)]*)\)/i), scale: part(/scale\(([^)]*)\)/i), rotate: part(/rotate\(([^)]*)\)/i) };
    cur[k] = v;
    set("transform", ["translateY", "scale", "rotate"].filter((n) => cur[n]).map((n) => `${n}(${cur[n]})`).join(" "));
  };
  const tr = /^(\S+)\s+(\d+)ms\s+(.+)$/.exec(g("transition"));
  const writeTr = (ms: string, timing: string) => set("transition", ms ? `all ${ms}ms ${timing || "ease"}` : "");
  return (
    <>
      <Row label="Transition" set={!!g("transition")} onReset={() => set("transition", "")}>
        <span className="cs-len">
          <input type="number" min={0} max={2000} step={50} aria-label="Durée de la transition (ms)" placeholder="150" value={tr?.[2] ?? ""} onChange={(e) => writeTr(e.target.value, tr?.[3] ?? "ease")} />
          <span className="cs-unit">ms</span>
          <select aria-label="Courbe de la transition" value={tr?.[3] ?? "ease"} onChange={(e) => writeTr(tr?.[2] ?? "150", e.target.value)}><option value="ease">Douce</option><option value="ease-in-out">Entrée/sortie</option><option value="linear">Linéaire</option></select>
        </span>
      </Row>
      <Row label="Échelle" set={!!part(/scale\(([^)]*)\)/i)} onReset={() => setT("scale", "")}><span className="cs-len"><input type="range" min={0.8} max={1.3} step={0.01} aria-label="Échelle" value={part(/scale\(([^)]*)\)/i) || 1} onChange={(e) => setT("scale", e.target.value === "1" ? "" : e.target.value)} /><output>{part(/scale\(([^)]*)\)/i) || "1"}</output></span></Row>
      <Row label="Rotation" set={!!part(/rotate\(([^)]*)\)/i)} onReset={() => setT("rotate", "")}><span className="cs-len"><input type="range" min={-30} max={30} step={1} aria-label="Rotation" value={parseFloat(part(/rotate\(([^)]*)\)/i)) || 0} onChange={(e) => setT("rotate", e.target.value === "0" ? "" : `${e.target.value}deg`)} /><output>{parseFloat(part(/rotate\(([^)]*)\)/i)) || 0}°</output></span></Row>
      <Row label="Décalage vertical" set={!!part(/translateY\(([^)]*)\)/i)} onReset={() => setT("translateY", "")}><span className="cs-len"><input type="range" min={-20} max={20} step={1} aria-label="Décalage vertical" value={parseFloat(part(/translateY\(([^)]*)\)/i)) || 0} onChange={(e) => setT("translateY", e.target.value === "0" ? "" : `${e.target.value}px`)} /><output>{parseFloat(part(/translateY\(([^)]*)\)/i)) || 0}px</output></span></Row>
      <Row label="Curseur" set={!!g("cursor")} onReset={() => set("cursor", "")}>
        <select aria-label="Curseur" value={g("cursor")} onChange={(e) => set("cursor", e.target.value)}><option value="">Par défaut</option>{["pointer", "default", "text", "not-allowed", "grab", "help"].map((c) => <option key={c} value={c}>{c}</option>)}</select>
      </Row>
      <Row label="Texte ajouté" set={!!g("content")} onReset={() => set("content", "")}><input className="cs-text" aria-label="Texte ajouté avant ou après (content)" placeholder={'"→ "'} value={g("content")} onChange={(e) => set("content", e.target.value)} /></Row>
    </>
  );
}

function Other({ rule, set }: Props) {
  const [prop, setProp] = useState("");
  const [val, setVal] = useState("");
  return (
    <>
      {(rule?.decls ?? []).map(([p, v]) => (
        <Row key={p} label={p} set onReset={() => set(p, "")} wide><input className="cs-text" aria-label={`Valeur de ${p}`} value={v} onChange={(e) => set(p, e.target.value)} /></Row>
      ))}
      <div className="cs-addrow">
        <select aria-label="Propriété à ajouter" value={prop} onChange={(e) => setProp(e.target.value)}><option value="">Ajouter une propriété…</option>{ALLOWED.map((p) => <option key={p} value={p}>{p}</option>)}</select>
        <input className="cs-text" aria-label="Valeur à ajouter" placeholder="valeur" value={val} onChange={(e) => setVal(e.target.value)} />
        <button type="button" className="cs-add" disabled={!prop || !val} onClick={() => { set(prop, val); setProp(""); setVal(""); }}><FormIcon name="plus" size={14} /> Ajouter</button>
      </div>
    </>
  );
}

const PANELS: Record<string, (p: Props) => React.ReactNode> = { type: Typography, color: Colors, border: Borders, spacing: Spacing, size: Size, shadow: ShadowsPanel, layout: LayoutPanel, effects: Effects, other: Other };

/** An icon rail (a dot marks the groups that hold a setting) and the controls of the chosen group. */
export function Inspector(props: Props & { active: string; onActive: (id: string) => void }) {
  const { active, onActive, rule, disabled } = props;
  const Panel = PANELS[active] ?? Typography;
  const used = (s: Section) => s.id === "other" ? (rule?.decls ?? []).some(([p]) => !SECTIONS.slice(0, -1).some((x) => x.props.test(p))) : (rule?.decls ?? []).some(([p]) => s.props.test(p));
  return (
    <div className="cs-inspector">
      <div className="cs-rail" role="tablist" aria-label="Groupes de réglages" aria-orientation="horizontal">
        {SECTIONS.map((s) => (
          <button key={s.id} type="button" role="tab" aria-selected={active === s.id} className="cs-tab" aria-label={s.label} title={s.label} onClick={() => onActive(s.id)}>
            <FormIcon name={s.icon} size={18} />{used(s) && <span className="cs-used" aria-hidden="true" />}
          </button>
        ))}
      </div>
      <fieldset className="cs-panel" disabled={disabled} aria-label={SECTIONS.find((s) => s.id === active)?.label}>
        <Panel {...props} />
      </fieldset>
    </div>
  );
}
