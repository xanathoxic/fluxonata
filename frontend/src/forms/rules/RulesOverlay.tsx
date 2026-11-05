import { useMemo, useState } from "react";
import { Overlay } from "../../components/Overlay";
import { FormIcon, IconButton, type FormIconName } from "../FormIcon";
import { exprToRuleSet } from "../logic";
import { describeEffect, isGroup, sentence, TOKEN_RE, type Cond, type CondOp, type Group, type Node, type Rule } from "../rules";
import type { FormField } from "../types";
import { COMPUTABLE } from "../calc";
import { ModeTabs } from "../ModeTabs";
import { CalcEditor, calcProblem } from "./CalcEditor";
import { GroupEditor, newCond, shape } from "./ConditionTree";
import { blankEffect, EffectList, unavailable } from "./Effects";
import { Tryout } from "./Tryout";

const MAP: Record<string, CondOp> = { "==": "equals", "!=": "not_equals", "<": "lt", "<=": "lte", ">": "gt", ">=": "gte", contains: "contains" };

/** The older single « show only if… » expression, as a rule (null when it is more elaborate than the builder can write). */
export function legacyAsRule(expr: string | undefined): Rule | null {
  const set = expr ? exprToRuleSet(expr) : null;
  if (!set) return null;
  const items: Cond[] = set.rules.map((r) => shape({ field: r.field, op: MAP[r.op] as CondOp, value: r.value }));
  return { name: "Règle d'affichage reprise", when: { op: set.join === "or" ? "any" : "all", items }, then: [{ do: "show" }], else: [{ do: "hide" }] };
}

const group = (fields: FormField[]): Group => ({ op: "all", items: [newCond(fields)] });
interface Template { id: string; label: string; icon: FormIconName; build: (fields: FormField[], field: FormField) => Rule }
const TEMPLATES: Template[] = [
  { id: "show", label: "Afficher seulement si…", icon: "eye", build: (f) => ({ when: group(f), then: [{ do: "show" }], else: [{ do: "hide" }] }) },
  { id: "hide", label: "Masquer si…", icon: "eyeOff", build: (f) => ({ when: group(f), then: [{ do: "hide" }] }) },
  { id: "require", label: "Rendre obligatoire si…", icon: "check", build: (f) => ({ when: group(f), then: [{ do: "require" }], else: [{ do: "optional" }] }) },
  { id: "text", label: "Changer un texte si…", icon: "paragraph", build: (f, field) => ({ when: group(f), then: [blankEffect("text", field)] }) },
  { id: "options", label: "Limiter les choix si…", icon: "list2", build: (f, field) => ({ when: group(f), then: [blankEffect("options", field)] }) },
  { id: "load", label: "Charger les données du service si…", icon: "link", build: (f, field) => ({ when: group(f), then: [blankEffect("load", field)] }) },
  { id: "free", label: "Règle libre", icon: "rule", build: (f) => ({ when: group(f), then: [] }) },
];

function problems(rule: Rule, fields: FormField[]): string[] {
  const out: string[] = [];
  const keys = new Set(fields.map((f) => f.key));
  const walk = (n: Node, at: string) => {
    if (isGroup(n)) { n.items.forEach((i, k) => walk(i, `${at}.${k + 1}`)); return; }
    if (n.template !== undefined) {
      if (n.template.trim() === "") out.push(`Condition ${at} : écrivez la valeur composée.`);
      for (const m of n.template.matchAll(TOKEN_RE)) if (!keys.has(m[1] as string)) out.push(`Condition ${at} : le champ « ${m[1]} » n'est plus disponible.`);
    } else if (!keys.has(n.field as string)) out.push(`Condition ${at} : le champ « ${n.field} » n'est plus disponible (supprimé, ou il lit déjà ce champ).`);
    if (n.ref !== undefined && !keys.has(n.ref)) out.push(`Condition ${at} : le champ comparé n'existe plus.`);
    if ((n.op === "in_list" || n.op === "includes_any") && (n.values ?? []).length === 0) out.push(`Condition ${at} : indiquez au moins une valeur.`);
  };
  walk(rule.when, "1");
  if (rule.then.length === 0) out.push("Ajoutez au moins un effet dans « Alors ».");
  return out;
}

interface Props { field: FormField; earlier: FormField[]; tab?: "rules" | "calc"; onApply: (next: FormField) => void; onClose: () => void }

/** The condition editor, in a window centred on the screen: rules with their conditions (groups, operators, comparison with a
 * value or another field) and their effects, written back as a sentence, with a way to try them. Nothing changes until « Appliquer ». */
export function RulesOverlay({ field, earlier, tab: firstTab = "rules", onApply, onClose }: Props) {
  const legacy = !field.rules?.length ? legacyAsRule(field.visible_if) : null;
  const keepLegacy = Boolean(field.visible_if) && !legacy; // a hand-written expression stays, untouched
  const [rules, setRules] = useState<Rule[]>(() => field.rules ?? (legacy ? [legacy] : []));
  const canCalc = (COMPUTABLE as string[]).includes(field.type);
  const [tab, setTab] = useState<"rules" | "calc">(canCalc ? firstTab : "rules");
  const [formula, setFormula] = useState(field.compute ?? "");
  const [sel, setSel] = useState(0);
  const [showElse, setShowElse] = useState(false);
  const label = (key: string) => earlier.find((f) => f.key === key)?.label || key;
  const rule = rules[sel];
  const set = (next: Rule) => setRules(rules.map((r, i) => (i === sel ? next : r)));
  const all = useMemo(() => rules.map((r) => problems(r, earlier)), [rules, earlier]);
  const blocked = all.some((p) => p.length > 0) || Boolean(calcProblem(formula, earlier));
  const add = (t: Template) => { setRules([...rules, { ...t.build(earlier, field), id: `r${Date.now().toString(36)}` }]); setSel(rules.length); };
  const move = (i: number, d: -1 | 1) => { const j = i + d; if (j < 0 || j >= rules.length) return; const c = [...rules]; [c[i], c[j]] = [c[j] as Rule, c[i] as Rule]; setRules(c); setSel(j); };
  const summary = (r: Rule) => r.name || `Quand ${sentence(r.when, label)}`;
  const apply = () => {
    const { rules: _old, visible_if: _legacy, compute: _calc, ...rest } = field;
    onApply({ ...rest, ...(formula.trim() ? { compute: formula.trim() } : {}), ...(keepLegacy && field.visible_if ? { visible_if: field.visible_if } : {}), ...(rules.length ? { rules } : {}) });
    onClose();
  };
  return (
    <Overlay variant="modal" wide title={`Conditions du champ « ${field.label || field.key} »`}
      description="Une règle dit quand elle s'applique (conditions sur la valeur d'un autre champ, où qu'il soit placé) et ce que le champ devient ; un calcul fixe sa valeur à partir des autres champs. Rien n'est modifié avant « Appliquer »."
      onClose={onClose}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="button" className="primary" onClick={apply} disabled={blocked}>Appliquer</button></>}>
      {canCalc && <div className="cc-tabs"><ModeTabs label="Contenu de la fenêtre" active={tab} onChange={(t) => setTab(t as "rules" | "calc")} tabs={[{ id: "rules", label: `Conditions${rules.length ? ` (${rules.length})` : ""}`, icon: "rule" }, { id: "calc", label: `Calcul${formula.trim() ? " ✓" : ""}`, icon: "braces" }]} /></div>}
      {tab === "calc" ? <CalcEditor field={field} others={earlier} formula={formula} onFormula={setFormula} /> : (
      <div className="rl">
        <nav className="rl-list" aria-label="Règles du champ">
          <h3>Règles</h3>
          {rules.length === 0 && <p className="hint">Aucune règle : le champ est toujours affiché, comme défini dans ses propriétés.</p>}
          {rules.map((r, i) => (
            <div key={r.id ?? i} className={`rl-item${i === sel ? " on" : ""}`}>
              <button type="button" aria-pressed={i === sel} className="rl-pick" onClick={() => setSel(i)}><span className="rl-n">{i + 1}</span><span className="rl-t">{summary(r)}</span>{all[i]?.length ? <FormIcon name="warn" size={14} /> : null}</button>
              <span className="rl-acts">
                <IconButton icon="up" label={`Monter la règle ${i + 1}`} disabled={i === 0} onClick={() => move(i, -1)} />
                <IconButton icon="down" label={`Descendre la règle ${i + 1}`} disabled={i === rules.length - 1} onClick={() => move(i, 1)} />
                <IconButton icon="trash" danger label={`Supprimer la règle ${i + 1}`} onClick={() => { setRules(rules.filter((_, k) => k !== i)); setSel(Math.max(0, sel - (i <= sel ? 1 : 0))); }} />
              </span>
            </div>
          ))}
          <h3>Nouvelle règle</h3>
          <div className="rl-templates" role="group" aria-label="Nouvelle règle">
            {TEMPLATES.map((t) => {
              const why = t.id === "options" || t.id === "load" ? unavailable(t.id, field) : "";
              return <button key={t.id} type="button" className="rl-tpl" disabled={Boolean(why) || earlier.length === 0} title={why || (earlier.length === 0 ? "Aucun autre champ à lire dans ce formulaire." : t.label)} onClick={() => add(t)}><FormIcon name={t.icon} size={16} />{t.label}</button>;
            })}
          </div>
          {earlier.length === 0 && <p className="hint">Les conditions lisent la valeur d'un <strong>autre champ</strong> du formulaire : il n'y en a pas encore.</p>}
          {keepLegacy && <p className="hint" role="note">Ce champ garde une règle d'affichage écrite en expression ({field.visible_if}), qui s'applique en plus.</p>}
          {legacy && <p className="hint" role="note">L'ancienne règle d'affichage de ce champ a été reprise comme règle 1.</p>}
        </nav>
        <div className="rl-edit">
          {!rule ? <p className="hint">Choisissez une règle, ou créez-en une avec un modèle.</p> : (
            <>
              <p className="rl-sentence" role="status" aria-label="Résumé de la règle"><strong>Quand</strong> {sentence(rule.when, label)}<br /><strong>alors</strong> {rule.then.length ? rule.then.map(describeEffect).join(" · ") : "…"}{rule.else?.length ? <><br /><strong>sinon</strong> {rule.else.map(describeEffect).join(" · ")}</> : null}</p>
              <div className="field"><label htmlFor="rl-name">Nom de la règle (facultatif)</label><input id="rl-name" value={rule.name ?? ""} maxLength={120} placeholder="ex. Seulement pour la France" onChange={(e) => set({ ...rule, name: e.target.value || undefined })} /></div>
              <h3><FormIcon name="rule" size={16} /> Quand</h3>
              {isGroup(rule.when) && earlier.length > 0 && <GroupEditor group={rule.when} fields={earlier} path={[]} onChange={(w) => set({ ...rule, when: w })} />}
              <h3><FormIcon name="check" size={16} /> Alors</h3>
              <EffectList label="Alors" effects={rule.then} field={field} earlier={earlier} onChange={(then) => set({ ...rule, then })} />
              <h3><button type="button" className="rl-disclose" aria-expanded={showElse || Boolean(rule.else?.length)} onClick={() => setShowElse(!showElse)}><FormIcon name={showElse || rule.else?.length ? "down" : "right"} size={14} /> Sinon</button></h3>
              {(showElse || Boolean(rule.else?.length)) && <EffectList label="Sinon" effects={rule.else ?? []} field={field} earlier={earlier} onChange={(e) => set({ ...rule, else: e.length ? e : undefined })} />}
              {all[sel]?.length ? <ul className="fb-problems" role="alert" aria-label="Problèmes de la règle">{(all[sel] as string[]).map((p, i) => <li key={i}>{p}</li>)}</ul> : null}
              <Tryout rule={rule} field={field} fields={earlier} />
            </>
          )}
        </div>
      </div>
      )}
    </Overlay>
  );
}
