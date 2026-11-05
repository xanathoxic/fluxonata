import { useEffect, useState } from "react";
import { TagInput } from "../../components/Tags";
import { CONTROL_ICONS, MAX_ITEMS, MAX_ROWS, MODES, producesOf, type ControlItem, type ControlSpec } from "../../forms/controls";
import { DefinitionEditor } from "../../forms/DefinitionEditor";
import { FormIcon, IconLabel, type FormIconName } from "../../forms/FormIcon";
import { isBlock, type FormDefinition, type FormField } from "../../forms/types";
import "./designer.css";
import { Card, ChoiceCards, Num, Panel, Switch } from "./DesignerUi";
import { DocsPanel } from "./DocsPanel";
import { OptionsEditor } from "./OptionsDesigner";
import { TableDesigner } from "./TableDesigner";

export type Cfg = Omit<ControlSpec, "parts">;
export interface Meta { name: string; description: string; icon: string; folder: string; tags: string[] }
type Section = "general" | "docs" | "options" | "rows" | "fields";

const WIDE = "(min-width: 1560px)";
const SIDE_KEY = "fluxonata.control.docs-side";
const useWide = (): boolean => {
  const [wide, setWide] = useState(() => typeof window !== "undefined" && window.matchMedia(WIDE).matches);
  useEffect(() => { const m = window.matchMedia(WIDE); const on = () => setWide(m.matches); m.addEventListener("change", on); return () => m.removeEventListener("change", on); }, []);
  return wide;
};
const savedSide = (): boolean => { try { return window.localStorage.getItem(SIDE_KEY) !== "0"; } catch { return true; } };

interface Props {
  meta: Meta; cfg: Cfg; row: FormDefinition; edit: boolean; problems: string[] | null;
  onMeta: (m: Meta) => void; onCfg: (c: Cfg) => void; onRow: (d: FormDefinition) => void;
}

/** The design of a control, one subject at a time: a short list of sections on the left, the selected one on the right with room to work
 * (the options as a tree, the sub-fields with the whole form editor). */
export function ControlDesigner({ meta, cfg, row, edit, problems, onMeta, onCfg, onRow }: Props) {
  const parts = (row.sections[0]?.fields ?? []) as FormField[];
  const listy = cfg.mode === "items" || cfg.mode === "choice";
  const [section, setSection] = useState<Section>("general");
  const [advanced, setAdvanced] = useState(false);
  const wide = useWide();
  const [sideOn, setSideOn] = useState(savedSide);
  const toggleSide = () => { const next = !sideOn; setSideOn(next); try { window.localStorage.setItem(SIDE_KEY, next ? "1" : "0"); } catch { /* storage unavailable */ } };
  const table = cfg.mode === "repeat" && cfg.display === "table";
  const patch = (p: Partial<Cfg>) => { const next = { ...cfg, ...p } as Record<string, unknown>; for (const k of Object.keys(next)) if (next[k] === undefined || next[k] === "") delete next[k]; onCfg(next as unknown as Cfg); };
  const produced = { object: "un objet", array: "une liste", string: "un texte (ou un objet si des options ont des détails)" }[producesOf({ ...cfg, parts })];
  const kind = MODES.find((m) => m.id === cfg.mode);
  const items: ControlItem[] = cfg.items ?? [];
  const cap = cfg.mode === "repeat" ? MAX_ROWS : MAX_ITEMS;
  const bad = problems?.length ?? 0;
  const model = { id: "fields" as const, icon: "design" as const, label: listy ? "Modèle d'une option" : table ? "Colonnes" : "Champs", count: parts.filter((p) => !isBlock(p.type)).length };
  const nav: { id: Section; icon: FormIconName; label: string; count?: number }[] = [
    { id: "general", icon: "info", label: "Général" },
    ...(listy ? [model, { id: "options" as const, icon: "list2" as const, label: "Options", count: items.length }] : []),
    ...(cfg.mode === "repeat" ? [{ id: "rows" as const, icon: "repeat" as const, label: "Lignes" }] : []),
    ...(listy ? [] : [model]),
    { id: "docs", icon: "braces", label: "Documentation" },
  ];
  const current: Section = nav.some((n) => n.id === section) ? section : "general";
  const side = wide && sideOn && current !== "docs";

  return (
    <div className={`cd${side ? " with-side" : ""}${wide && !sideOn && current !== "docs" ? " has-doc-btn" : ""}`}>
      <nav className="cd-nav" aria-label="Sections du contrôle">
        <ul>
          {nav.map((n) => (
            <li key={n.id}>
              <button type="button" className={`cd-nav-i${current === n.id ? " on" : ""}`} aria-current={current === n.id ? "page" : undefined} onClick={() => setSection(n.id)}>
                <FormIcon name={n.icon} size={18} /><span className="cd-nav-t">{n.label}</span>{n.count !== undefined && <span className="cd-nav-n" aria-label={`${n.count} élément(s)`}>{n.count}</span>}
              </button>
            </li>
          ))}
        </ul>
        <p className={`cd-nav-v ${bad ? "bad" : "ok"}`} role="status"><FormIcon name={bad ? "warn" : "check"} size={14} /> {problems === null ? "Vérification…" : bad ? `${bad} problème(s) à corriger` : "Contrôle valide"}</p>
      </nav>
      <div className="cd-main">
        <fieldset disabled={!edit} className="cd-fs">
          {current === "general" && (
            <Panel icon="info" title="Général" subtitle="Comment ce contrôle est présenté dans la bibliothèque et dans la palette de l'éditeur de formulaires.">
              <Card icon="edit" title="Identité">
                <div className="field"><label htmlFor="cs-name">Nom</label><input id="cs-name" value={meta.name} maxLength={160} onChange={(e) => onMeta({ ...meta, name: e.target.value })} /></div>
                <div className="field"><label htmlFor="cs-desc">Description</label><textarea id="cs-desc" rows={3} value={meta.description} placeholder="À quoi sert ce contrôle, quand l'utiliser…" onChange={(e) => onMeta({ ...meta, description: e.target.value })} /></div>
                <div className="field"><label htmlFor="cs-folder">Dossier</label><input id="cs-folder" value={meta.folder} maxLength={200} placeholder="Ex. : Adresses/Clients" onChange={(e) => onMeta({ ...meta, folder: e.target.value })} /></div>
              </Card>
              <Card icon={kind?.icon ?? "layers"} title="Type" help="Le type est choisi à la création et ne peut plus changer : pour un autre type, créez un nouveau contrôle.">
                <p className="cd-kind"><strong>{kind?.label}</strong> — {kind?.hint}</p>
                <p className="cd-note" role="note"><FormIcon name="info" size={15} /><span>Valeur remise au workflow : <strong>{produced}</strong>.</span></p>
              </Card>
              <Card icon="star" title="Icône" help="L'icône s'affiche dans la bibliothèque, la palette de l'éditeur de formulaires et sur le champ.">
                <div className="cd-icons" role="radiogroup" aria-label="Icône">
                  {CONTROL_ICONS.map((n) => (
                    <label key={n} className={`cd-icon${meta.icon === n ? " on" : ""}`} title={n}>
                      <input type="radio" name="cs-icon" value={n} aria-label={n} checked={meta.icon === n} onChange={() => onMeta({ ...meta, icon: n })} /><FormIcon name={n} size={20} />
                    </label>
                  ))}
                </div>
              </Card>
              <Card icon="tag" title="Étiquettes" help="Pour retrouver le contrôle dans la bibliothèque.">
                <TagInput value={meta.tags} onChange={(tags) => onMeta({ ...meta, tags })} suggestions={[]} />
              </Card>
            </Panel>
          )}

          {current === "docs" && (
            <Panel icon="braces" title="Documentation" subtitle="Générée depuis le contrôle tel qu'il est conçu : elle suit chaque modification, sans rien à saisir.">
              <DocsPanel spec={{ ...cfg, parts } as ControlSpec} />
            </Panel>
          )}

          {current === "options" && listy && (
            <Panel fill icon="list2" title="Options" subtitle="Les valeurs proposées : un libellé, une valeur, un groupe. Chaque option reprend le modèle dessiné dans « Modèle d'une option ».">
              <OptionsEditor items={items} edit={edit} onChange={(next) => patch({ items: next })} />
              <div className="cd-grid2">
                <Card icon="eye" title="Affichage">
                  <ChoiceCards name="cs-display" label="Affichage" columns={cfg.mode === "choice" ? 3 : 2} value={cfg.display ?? (cfg.mode === "choice" ? "select" : "list")} onChange={(d) => patch({ display: d === "list" || d === "select" ? undefined : d })} disabled={!edit}
                    options={cfg.mode === "choice"
                      ? [{ id: "select", title: "Liste déroulante", hint: "Un menu" }, { id: "dropdown", title: "Menu avec recherche", hint: "Recherche et groupes" }, { id: "radio", title: "Boutons radio", hint: "Tout visible" }]
                      : [{ id: "list", title: "Liste", hint: "Toutes les cases visibles" }, { id: "dropdown", title: "Menu avec recherche", hint: "Recherche, groupes, pastilles" }]} />
                  {cfg.display === "dropdown" && <div className="field"><label htmlFor="cs-ph">Texte du champ de recherche</label><input id="cs-ph" value={cfg.placeholder ?? ""} maxLength={120} placeholder="Rechercher…" onChange={(e) => patch({ placeholder: e.target.value })} /></div>}
                </Card>
                <Card icon="sliders" title="Comportement">
                  {cfg.mode === "items" && (
                    <div className="cd-pair">
                      <Num id="cs-min" label="Au moins coché" value={cfg.min} max={cap} onChange={(v) => patch({ min: v })} />
                      <Num id="cs-max" label="Au plus coché" value={cfg.max} max={cap} onChange={(v) => patch({ max: v })} />
                    </div>
                  )}
                  {cfg.mode === "items" && <Switch id="cs-ed" checked={Boolean(cfg.items_editable)} onChange={(v) => patch({ items_editable: v ? true : undefined, ...(v ? { dynamic: undefined } : {}) })} label="Liste modifiable par le formulaire" help="Le concepteur du formulaire qui utilise ce contrôle peut changer la liste d'options (les options fixes servent alors de valeurs par défaut)." />}
                  <Switch id="cs-dyn" checked={Boolean(cfg.dynamic)} onChange={(v) => patch({ dynamic: v ? true : undefined, ...(v ? { items_editable: undefined } : {}) })} label="Options fournies par le formulaire" help="Le contrôle ne porte aucun appel d'API : chaque formulaire qui l'utilise peut lire son propre service pour fournir les options (réglages du champ, « Données depuis un service »)." />
                </Card>
              </div>
            </Panel>
          )}

          {current === "rows" && cfg.mode === "repeat" && (
            <Panel icon="repeat" title="Lignes répétées" subtitle="Le même groupe de champs, répété autant de fois que nécessaire.">
              <Card icon="eye" title="Affichage">
                <ChoiceCards name="cs-rdisplay" label="Affichage des lignes" value={cfg.display ?? "cards"} onChange={(d) => patch({ display: d === "cards" ? undefined : d, ...(d === "cards" ? { totals: undefined } : {}) })} disabled={!edit}
                  options={[{ id: "cards", icon: "layers", title: "Fiches", hint: "Un bloc par ligne" }, { id: "table", icon: "datagrid", title: "Tableau", hint: "Une rangée par ligne, un champ par colonne (liste déroulante comprise)" }]} />
              </Card>
              <div className="cd-grid2">
                <Card icon="sliders" title="Limites">
                  <div className="cd-pair"><Num id="cs-min" label="Au moins" value={cfg.min} max={cap} onChange={(v) => patch({ min: v })} /><Num id="cs-max" label="Au plus" value={cfg.max} max={cap} placeholder={String(cap)} onChange={(v) => patch({ max: v })} /></div>
                </Card>
                <Card icon="paragraph" title="Textes">
                  <div className="field"><label htmlFor="cs-rl">Titre d'une ligne</label><input id="cs-rl" value={cfg.row_label ?? ""} maxLength={60} placeholder="Ligne" onChange={(e) => patch({ row_label: e.target.value })} /></div>
                  <div className="field"><label htmlFor="cs-al">Bouton d'ajout</label><input id="cs-al" value={cfg.add_label ?? ""} maxLength={60} placeholder="Ajouter une ligne" onChange={(e) => patch({ add_label: e.target.value })} /></div>
                </Card>
              </div>
            </Panel>
          )}

          {current === "fields" && table && !advanced && (
            <Panel icon="datagrid" title="Colonnes du tableau" subtitle="Chaque colonne est un champ de la ligne : choisissez-la dans le bandeau pour la régler, et voyez le tableau en dessous.">
              <TableDesigner cfg={cfg} parts={parts} edit={edit} onAdvanced={() => setAdvanced(true)} onTotals={(totals) => patch({ totals })}
                onParts={(next) => onRow({ ...row, sections: [{ ...(row.sections[0] ?? { title: "" }), fields: next }, ...row.sections.slice(1)] })} />
            </Panel>
          )}

          {current === "fields" && !(table && !advanced) && (
            <Panel fill icon="design" title={listy ? "Modèle d'une option" : cfg.mode === "repeat" ? "Champs d'une ligne" : "Champs du groupe"}
              subtitle={listy ? "On dessine une seule fois l'option et ses enfants : toutes les options de la liste en sont la répétition, avec leurs propres valeurs." : "Les champs réunis par ce contrôle, avec leurs conditions, règles et calculs."}>
              {listy && <p className="cd-root" role="note"><FormIcon name={cfg.mode === "items" ? "boxes" : "list2"} size={18} /> <strong>Une option</strong> (libellé et valeur) <span className="cd-root-a">ouvre ces champs enfants :</span></p>}
              {table && <p><button type="button" onClick={() => setAdvanced(false)}><IconLabel icon="datagrid">Retour à l'éditeur de colonnes</IconLabel></button></p>}
              <div className={`cd-editor${listy ? " cd-children" : ""}`}><DefinitionEditor control def={row} onChange={onRow} edit={edit} schema={undefined} connections={[]} /></div>
            </Panel>
          )}
        </fieldset>
      </div>
      {wide && current !== "docs" && !sideOn && (
        <button type="button" className="cd-doc-btn" aria-pressed={false} aria-label="Afficher la documentation" title="Afficher la documentation" onClick={toggleSide}><FormIcon name="braces" size={16} /></button>
      )}
      {side && (
        <aside className="cd-side" aria-label="Documentation en direct">
          <header className="cd-side-h"><h2><FormIcon name="braces" size={16} /> Documentation</h2><button type="button" onClick={toggleSide} aria-label="Masquer la documentation" title="Masquer la documentation"><FormIcon name="close" size={16} /></button></header>
          <DocsPanel spec={{ ...cfg, parts } as ControlSpec} />
        </aside>
      )}
    </div>
  );
}
