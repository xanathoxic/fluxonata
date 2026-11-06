import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type ApiError } from "../api";
import { useApp } from "../context";
import { useDraft } from "./DraftContext";
import type { GraphNode } from "./types";
import { HelpTip } from "../components/HelpTip";

interface Tested { ok: boolean; output: Record<string, unknown> | null; error: string | null; source: string }
const pretty = (v: unknown) => JSON.stringify(v, null, 2);

/** Data of one step: pinning (development shortcut) and a test of the step. Outputs of past runs are deliberately not shown here: they may hold secrets. */
export function DataTab({ node }: { node: GraphNode }) {
  const { workspace } = useApp();
  const d = useDraft();
  const base = `/workspaces/${workspace.slug}/workflows/${d.slug}/debug`;
  const test = useMutation<Tested, ApiError>({ mutationFn: () => api<Tested>(`${base}/test-step/`, { json: { node_id: node.id, graph: d.graph } }) });
  const navigate = useNavigate();
  const [inputsText, setInputsText] = useState<string | null>(null);
  const until = useMutation<{ id: string }, ApiError>({
    mutationFn: () => api(`/workspaces/${workspace.slug}/workflows/${d.slug}/runs/`, { json: { environment: "dev", inputs: JSON.parse(inputsText ?? "{}"), draft: true, until: node.id } }),
    onSuccess: (r) => navigate(`/runs/${r.id}`),
  });
  const [draftPin, setDraftPin] = useState<string | null>(null);
  const pin = d.graph.pins?.[node.id];
  const setPin = (v: Record<string, unknown> | undefined) => {
    const pins = { ...(d.graph.pins ?? {}) };
    if (v === undefined) delete pins[node.id]; else pins[node.id] = v;
    d.setGraph({ ...d.graph, pins: Object.keys(pins).length ? pins : undefined });
    setDraftPin(null);
  };
  let pinError = "";
  if (draftPin !== null) { try { const p = JSON.parse(draftPin); if (typeof p !== "object" || p === null || Array.isArray(p)) pinError = "Un objet JSON est attendu."; } catch { pinError = "JSON invalide."; } }
  return (
    <div>
      <h3>Donnée épinglée <HelpTip>Une sortie épinglée remplace l'étape dans les aperçus et dans les exécutions en <strong>développement</strong> (l'étape n'est alors pas exécutée) : elle permet de mettre au point la suite sans rejouer les effets. Jamais utilisée en recette ni en production.</HelpTip></h3>
      {pin !== undefined && draftPin === null && <pre className="mono data-box" tabIndex={0} aria-label="Donnée épinglée">{pretty(pin)}</pre>}
      {draftPin !== null && (
        <div className="field"><label htmlFor="pin-json">Sortie épinglée (JSON)</label>
          <textarea id="pin-json" rows={6} spellCheck={false} value={draftPin} onChange={(e) => setDraftPin(e.target.value)} />
          {pinError && <p role="alert" className="field-error">{pinError}</p>}
          <button type="button" className="primary" disabled={Boolean(pinError)} onClick={() => setPin(JSON.parse(draftPin) as Record<string, unknown>)}>Enregistrer</button> <button type="button" onClick={() => setDraftPin(null)}>Annuler</button></div>
      )}
      {d.editable && draftPin === null && (
        <p>
          <button type="button" onClick={() => setDraftPin(pretty(pin ?? {}))}>{pin !== undefined ? "Modifier la donnée épinglée" : "Saisir une donnée à épingler"}</button> {pin !== undefined && <button type="button" onClick={() => setPin(undefined)}>Désépingler</button>}
        </p>
      )}
      <h3>Exécuter jusqu'ici <HelpTip>Lance le <strong>brouillon enregistré</strong> en environnement de <strong>développement</strong> et s'arrête après cette étape : les étapes épinglées ne sont pas exécutées, les autres le sont réellement.</HelpTip></h3>
      {inputsText === null ? <button type="button" onClick={() => setInputsText("{}")}>Préparer l'exécution…</button> : (
        <div className="field"><label htmlFor="until-in">Entrées de l'essai (JSON)</label>
          <textarea id="until-in" rows={4} spellCheck={false} value={inputsText} onChange={(e) => setInputsText(e.target.value)} />
          <button type="button" className="primary" onClick={() => until.mutate()} disabled={until.isPending}>Exécuter jusqu'à « {node.name} »</button>
          {until.error && <p role="alert" className="field-error">Erreur : {until.error.message}</p>}</div>
      )}
      <h3>Tester cette étape <HelpTip>Les décisions, choix et transformations sont calculés sur ces données ; une étape qui a un effet n'est jamais rejouée ici.</HelpTip></h3>
      <button type="button" onClick={() => test.mutate()} disabled={test.isPending}>Tester avec ces données</button>
      {test.error && <p role="alert" className="field-error">Erreur : {test.error.message}</p>}
      {test.data && (test.data.ok
        ? <><p role="status">Résultat ({test.data.source}) :</p><pre className="mono data-box" tabIndex={0} aria-label="Résultat du test">{pretty(test.data.output)}</pre></>
        : <p role="alert" className="field-error">{test.data.error}</p>)}
    </div>
  );
}
