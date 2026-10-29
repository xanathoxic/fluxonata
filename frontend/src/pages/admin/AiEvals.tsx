import { useState } from "react";
import { usePaged } from "../../components/AdminUi";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { errorText, useWsMutation, useWsQuery } from "../../lib";
import { HelpTip } from "../../components/HelpTip";

interface Row { id: number; provider: string; model: string; suite: string; status: string; passed: number; total: number; score: number | null; tokens: number; prompts: Record<string, string>; previous_score: number | null; regression: boolean; error: string; created_at: string }
interface Providers { results: { name: string; model: string; kind: string; enabled: boolean }[] }
const SUITES: Record<string, string> = { search: "Recherche dans le catalogue (rapide)", workflows: "Génération de workflows complexes (lent)", edits: "Modifications par conversation", vague: "Demandes en mots simples (direct ou après le plan)" };
const STATUS: Record<string, string> = { queued: "En attente", running: "En cours…", done: "Terminée", failed: "Échouée" };

/** Scores of the assistant on the fixed case sets, by provider/model and prompt versions; a drop is flagged. */
export function AiEvals() {
  const { workspace } = useApp();
  const runs = useWsQuery<{ results: Row[] }>("ai-evals", "/ai/evals/", { refetchInterval: 4000 });
  const prov = useWsQuery<Providers>("aiproviders", "/ai/providers/");
  const start = useWsMutation<{ provider: string; suite: string; repeat: number }>("POST", "/ai/evals/", ["ai-evals"]);
  const [f, setF] = useState({ provider: "", suite: "search", repeat: 1 });
  const [open, setOpen] = useState<number | null>(null);
  const [text, setText] = useState("");
  const showLog = async (id: number) => {
    if (open === id) { setOpen(null); return; }
    setOpen(id);
    setText("");
    const res = await fetch(`/api/v1/workspaces/${workspace.slug}/ai/evals/${id}/`, { credentials: "same-origin" });
    setText(res.ok ? ((await res.json()) as { log: string; error: string }).log || "(journal vide)" : "Journal indisponible.");
  };
  const providers = (prov.data?.results ?? []).filter((p) => p.enabled);
  const paged = usePaged(runs.data?.results ?? [], 5);
  return (
    <section aria-labelledby="ev" className="adm-card">
      <h3 id="ev">Évaluations de l'assistant <HelpTip>Mesure la qualité sur des cas fixes, avec des contrôles structurels (pas la qualité rédactionnelle). Un score plus bas que le précédent du même fournisseur et de la même suite est signalé. Une évaluation appelle le modèle : <strong>coût et durée réels</strong> avec un service cloud. Avec le faux fournisseur de test, le score ne mesure rien de réel.</HelpTip></h3>
      <div className="table-wrap"><table className="adm-table"><caption className="sr-only">Évaluations</caption>
        <thead><tr><th scope="col">Date</th><th scope="col">Fournisseur (modèle)</th><th scope="col">Suite</th><th scope="col">Score</th><th scope="col">Prompts</th><th scope="col"><span className="sr-only">Journal</span></th></tr></thead>
        <tbody>{paged.slice.map((r) => (
          <tr key={r.id}><td>{formatDate(r.created_at)}</td><td>{r.provider}{r.model ? ` (${r.model})` : ""}</td><td>{SUITES[r.suite]?.split(" (")[0] ?? r.suite}</td>
            <td>{r.status !== "done" ? STATUS[r.status] + (r.error ? ` — ${r.error}` : "") : <>{r.passed}/{r.total}{r.score !== null ? ` (${r.score} %)` : ""} {r.regression && <strong role="status">⚠ régression (précédent : {r.previous_score} %)</strong>}<br /><span className="hint">{r.tokens.toLocaleString("fr-FR")} jetons</span></>}</td>
            <td className="hint">{Object.entries(r.prompts).filter(([k]) => k.includes("draft") || k.includes("edit")).map(([k, v]) => `${k} v${v}`).join(", ")}</td>
            <td><button type="button" onClick={() => void showLog(r.id)} aria-expanded={open === r.id}>{open === r.id ? "Masquer" : "Journal"}</button></td></tr>))}
          {runs.data?.results.length === 0 && <tr><td colSpan={6} className="hint">Aucune évaluation lancée. Les résultats ne sont pas mesurés tant qu'aucune n'a tourné sur un vrai modèle.</td></tr>}</tbody></table></div>
      {paged.pager("Pagination des évaluations")}
      {open !== null && <pre className="mono data-box" tabIndex={0} aria-label="Journal de l'évaluation">{text}</pre>}
      <form className="adm-evform" onSubmit={(e) => { e.preventDefault(); start.mutate(f); }}>
        <div className="field"><label htmlFor="ev-p">Fournisseur</label><select id="ev-p" value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value })} required><option value="">Choisir…</option>{providers.map((p) => <option key={p.name} value={p.name}>{p.name}{p.model ? ` (${p.model})` : ""}</option>)}</select></div>
        <div className="field"><label htmlFor="ev-s">Suite de cas</label><select id="ev-s" value={f.suite} onChange={(e) => setF({ ...f, suite: e.target.value })}>{Object.entries(SUITES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></div>
        <div className="field"><label htmlFor="ev-r">Passes par cas (un modèle varie d'une passe à l'autre)</label><input id="ev-r" type="number" min={1} max={3} value={f.repeat} onChange={(e) => setF({ ...f, repeat: Number(e.target.value) })} /></div>
        <button type="submit" className="primary" disabled={start.isPending || !f.provider}>Lancer l'évaluation</button>
        {start.error && <p role="alert" className="field-error">Erreur : {errorText(start.error)}</p>}
      </form>
    </section>
  );
}
