import { useEffect, useRef, useState } from "react";
import { api, type ApiError } from "../api";
import { useApp } from "../context";
import { FormIcon, IconLabel } from "./FormIcon";

interface Problem { line: number; message: string }
const SNIPPETS: { label: string; css: string }[] = [
  { label: "Titres en couleur", css: ".fb-heading {\n  color: #1d4ed8;\n  letter-spacing: .02em;\n}" },
  { label: "Libellés en gras", css: "label, .fb-label {\n  font-weight: 700;\n}" },
  { label: "Champs à fond clair", css: "input, select, textarea {\n  background: #f8fafc;\n  border: 1px solid #cbd5e1;\n}" },
  { label: "Un champ précis", css: '[data-key="nom"] input {\n  border-color: #1d4ed8;\n}' },
  { label: "Petits écrans", css: "@media (max-width: 600px) {\n  .fb-section {\n    padding: 8px;\n  }\n}" },
];
const HOOKS: [string, string][] = [
  [".fb-section", "une section"], [".fb-grid", "la grille d'une section"], [".fb-cell", "la case d'un champ"], [".fb-field", "un champ et son libellé"],
  [".fb-heading / .fb-paragraph / .fb-divider", "les blocs de contenu"], [".fb-actions", "la zone du bouton d'envoi"], [".fb-choice", "une option radio ou case"],
  ['[data-key="clé"] / [data-type="select"]', "un champ par sa clé ou son type"], ["&", "la racine du formulaire (&:hover, &.fb-l-left…)"], ["--fb-gap, --fb-pad, --fb-radius", "espacements et coins (propriétés personnalisées)"],
];

/** The author's own CSS. The server reads it with a strict grammar and writes back a canonical, scoped text (see
 * `triggers/formcss.py`): what is applied is that text, and only while it is valid. Nothing here reaches outside the form. */
export function CssEditor({ css, onChange, edit }: { css: string | undefined; onChange: (css: string | undefined) => void; edit: boolean }) {
  const { workspace } = useApp();
  const [text, setText] = useState(css ?? "");
  const [problems, setProblems] = useState<Problem[]>([]);
  const [busy, setBusy] = useState(false);
  const applied = useRef(css ?? "");
  const seq = useRef(0);

  // an outside change (undo, reload) replaces the text; our own applied value does not
  useEffect(() => { if ((css ?? "") !== applied.current) { applied.current = css ?? ""; setText(css ?? ""); setProblems([]); } }, [css]);

  const check = (value: string) => {
    setText(value);
    const mine = ++seq.current;
    setBusy(true);
    window.setTimeout(() => {
      if (mine !== seq.current) return;
      api<{ css: { css: string; problems: Problem[] } }>(`/workspaces/${workspace.slug}/form-library/check/`, { json: { css: value } })
        .then((r) => {
          if (mine !== seq.current) return;
          setBusy(false);
          setProblems(r.css.problems);
          if (r.css.problems.length === 0) { applied.current = r.css.css; onChange(r.css.css || undefined); }
        })
        .catch((e: ApiError) => { if (mine === seq.current) { setBusy(false); setProblems([{ line: 1, message: e?.status === 401 || e?.status === 403 ? "Votre session a expiré : reconnectez-vous (le texte reste dans cette page)." : "Vérification impossible pour le moment : le texte reste dans cette page." }]); } });
    }, 450);
  };
  const insert = (snippet: string) => check(text.trim() ? `${text.trimEnd()}\n\n${snippet}\n` : `${snippet}\n`);
  const lines = text.split("\n").length;
  return (
    <div className="fb-css">
      <div className="fb-csshead">
        <span className={`fb-pill ${problems.length ? "bad" : busy ? "wait" : "ok"}`} role="status">
          <FormIcon name={problems.length ? "warn" : "check"} size={14} />
          {problems.length ? `${problems.length} problème(s) : le dernier CSS valide reste appliqué` : busy ? "Vérification…" : text.trim() ? "CSS valide et appliqué" : "Aucun CSS personnalisé"}
        </span>
        <span className="hint">{lines} ligne(s)</span>
      </div>
      <label htmlFor="css-src" className="sr-only">CSS personnalisé</label>
      <textarea id="css-src" className="fb-csssrc" spellCheck={false} rows={16} value={text} disabled={!edit} onChange={(e) => check(e.target.value)} aria-describedby={problems.length ? "css-problems" : "css-help"} placeholder={".fb-heading {\n  color: #1d4ed8;\n}"} />
      {problems.length > 0 && (
        <ul id="css-problems" role="alert" className="fb-problems">{problems.map((p, i) => <li key={i}><strong>Ligne {p.line}</strong> : {p.message}</li>)}</ul>
      )}
      <p id="css-help" className="hint">Les règles s'appliquent uniquement à l'intérieur du formulaire. Sont refusés : <code>url()</code>, <code>@import</code>, <code>position: fixed</code>, <code>!important</code>, les échappements et tout ce qui n'est pas de la mise en forme. Le texte enregistré est réécrit sous forme normalisée.</p>
      {edit && (
        <div className="fb-snips" role="group" aria-label="Exemples à insérer">
          {SNIPPETS.map((s) => <button key={s.label} type="button" onClick={() => insert(s.css)}><IconLabel icon="plus">{s.label}</IconLabel></button>)}
          <button type="button" onClick={() => check("")} disabled={!text}><IconLabel icon="undo">Effacer</IconLabel></button>
        </div>
      )}
      <details className="fb-hooks">
        <summary><FormIcon name="info" size={16} /> Éléments que vous pouvez cibler</summary>
        <dl>{HOOKS.map(([k, v]) => <div key={k}><dt><code>{k}</code></dt><dd>{v}</dd></div>)}</dl>
      </details>
    </div>
  );
}
