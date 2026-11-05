import { useMemo, useRef, useState } from "react";
import { check, evaluate, FUNCTION_DOCS, MAX_FORMULA, refs, valueFor } from "../calc";
import { FormIcon } from "../FormIcon";
import { textOf } from "../rules";
import type { FormField } from "../types";

const EXAMPLES: { label: string; formula: (f: FormField[]) => string }[] = [
  { label: "Produit de deux champs", formula: (f) => `{${f[0]?.key ?? "a"}} * {${f[1]?.key ?? f[0]?.key ?? "b"}}` },
  { label: "Total avec 20 % de TVA", formula: (f) => `round({${f[0]?.key ?? "ht"}} * 1.2, 2)` },
  { label: "Nom complet", formula: (f) => `{${f[0]?.key ?? "prenom"}} & " " & upper({${f[1]?.key ?? f[0]?.key ?? "nom"}})` },
  { label: "Texte selon une condition", formula: (f) => `if({${f[0]?.key ?? "age"}} >= 18, "majeur", "mineur")` },
];

/** « "" when the formula is acceptable (an empty formula means no calculation), else why not. */
export const calcProblem = (formula: string, others: FormField[]): string => (formula.trim() === "" ? "" : check(formula, new Set(others.map((f) => f.key))));

interface Props { field: FormField; others: FormField[]; formula: string; onFormula: (f: string) => void }

/** The formula editor of a calculated field (a tab of the conditions window): the formula, the fields and functions to insert, and a live
 * try-out. The result is always computed by the server too: nothing typed in the field is ever kept. */
export function CalcEditor({ field, others, formula, onFormula }: Props) {
  const [sample, setSample] = useState<Record<string, string>>({});
  const area = useRef<HTMLTextAreaElement>(null);
  const problem = formula.trim() === "" ? "Écrivez une formule (ou laissez vide : le champ est alors saisi)." : calcProblem(formula, others);
  const used = useMemo(() => others.filter((f) => refs(formula).has(f.key)), [others, formula]);

  const insert = (text: string) => {
    const el = area.current;
    const from = el?.selectionStart ?? formula.length;
    const to = el?.selectionEnd ?? from;
    onFormula(`${formula.slice(0, from)}${text}${formula.slice(to)}`.slice(0, MAX_FORMULA));
    requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange(from + text.length, from + text.length); });
  };
  const values = Object.fromEntries(used.map((f) => [f.key, f.type === "number" ? (sample[f.key] === undefined || sample[f.key] === "" ? undefined : Number(sample[f.key])) : sample[f.key]]));
  const raw = problem ? null : evaluate(formula, values);
  const result = valueFor(field.type, raw);
  const groups = [...new Set(FUNCTION_DOCS.map((d) => d.group))];

  return (
    <div className="rl cc">
      <nav className="rl-list" aria-label="Champs et fonctions">
        <h3><FormIcon name="link" size={14} /> Champs</h3>
        {others.length === 0 && <p className="hint">Aucun autre champ à lire.</p>}
        <div className="cc-chips">{others.map((f) => <button key={f.key} type="button" className="rl-chip" title={`Insérer {${f.key}}`} onClick={() => insert(`{${f.key}}`)}>{f.label || f.key}</button>)}</div>
        <h3><FormIcon name="wand" size={14} /> Exemples</h3>
        <div className="cc-chips">{EXAMPLES.map((e) => <button key={e.label} type="button" className="rl-tpl" onClick={() => onFormula(e.formula(others))}>{e.label}</button>)}</div>
      </nav>
      <div className="rl-edit">
        <label htmlFor="cc-formula"><strong>Formule</strong></label>
        <textarea id="cc-formula" ref={area} className="cc-formula" rows={4} spellCheck={false} maxLength={MAX_FORMULA} value={formula} placeholder="ex. {prix} * {quantite}" aria-describedby="cc-status" onChange={(e) => onFormula(e.target.value)} />
        <p id="cc-status" role="status" className={problem ? "cc-bad" : "hint"}>{problem || "Formule valide."}</p>
        <p className="hint">Opérateurs : <code>+ - * / %</code>, <code>&amp;</code> (mettre bout à bout), <code>= &lt;&gt; &lt; &lt;= &gt; &gt;=</code>. Un champ s'écrit <code>{"{champ}"}</code>, un texte <code>"entre guillemets"</code>. Si un champ lu est vide ou n'est pas un nombre, le calcul n'a pas de valeur.</p>

        <h3><FormIcon name="play" size={14} /> Essayer</h3>
        {used.length === 0 ? <p className="hint">La formule ne lit aucun champ.</p> : (
          <div className="rl-tryfields">
            {used.map((f) => (
              <label key={f.key} className="rl-tryf">{f.label || f.key}
                <input type={f.type === "number" ? "number" : "text"} step="any" value={sample[f.key] ?? ""} onChange={(e) => setSample({ ...sample, [f.key]: e.target.value })} />
              </label>
            ))}
          </div>
        )}
        <p className="rl-sum" role="status" aria-label="Résultat de l'essai">Résultat : {problem ? "—" : result === null ? "pas de valeur (un champ lu est vide ou inadapté)" : <strong>{textOf(result)}</strong>}</p>

        <h3><FormIcon name="braces" size={14} /> Fonctions</h3>
        {groups.map((g) => (
          <div key={g} className="cc-fgroup" role="group" aria-label={g}>
            <span className="cc-gname">{g}</span>
            {FUNCTION_DOCS.filter((d) => d.group === g).map((d) => <button key={d.name} type="button" className="rl-chip" title={`${d.usage} — ${d.help} Ex. ${d.example}`} onClick={() => insert(`${d.name}(`)}>{d.name}</button>)}
          </div>
        ))}
      </div>
    </div>
  );
}
