import { useEffect, useMemo, useRef, useState } from "react";
import { api, type ApiError, type Execution } from "../api";
import { CodeEditor, type Completion } from "../components/CodeEditor";
import { Tabs } from "../components/Tabs";
import { useFocusTrap } from "../components/useFocusTrap";
import { useApp } from "../context";
import { availableVariables } from "./graphOps";
import { BindingsEditor, OutputsEditor } from "./ScriptParts";
import type { Action } from "../api";
import type { Graph, GraphNode, ScriptLanguage } from "./types";

interface Props {
  node: GraphNode;
  graph: Graph;
  actions: Action[];
  slug: string;
  editable: boolean;
  onUpdate: (patch: Partial<GraphNode>) => void;
  onClose: () => void;
}

type ConsoleTab = "essai" | "journal" | "resultat";
type Section = "code" | "io" | "test";
const TYPE_CHECK: Record<string, (v: unknown) => boolean> = {
  string: (v) => typeof v === "string", integer: (v) => Number.isInteger(v), number: (v) => typeof v === "number", boolean: (v) => typeof v === "boolean",
  array: Array.isArray, object: (v) => typeof v === "object" && v !== null && !Array.isArray(v),
};

export function ScriptEditorDialog({ node, graph, actions, slug, editable, onUpdate, onClose }: Props) {
  const { workspace } = useApp();
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, onClose);
  const variables = availableVariables(graph, node.id, actions);
  const [tab, setTab] = useState<ConsoleTab>("essai");
  const [section, setSection] = useState<Section>("code");
  const bindingNames = Object.keys(node.bindings ?? {});
  const [sampleBindings, setSampleBindings] = useState(() => JSON.stringify(Object.fromEntries(bindingNames.map((n) => [n, typeof node.bindings?.[n] === "string" && !String(node.bindings?.[n]).includes("{{") ? node.bindings?.[n] : ""])), null, 2));
  const [sampleInputs, setSampleInputs] = useState(() => JSON.stringify(Object.fromEntries(Object.entries(graph.inputs.properties ?? {}).map(([k, p]) => [k, p.default ?? (p.type === "integer" || p.type === "number" ? 0 : p.type === "boolean" ? false : "")])), null, 2));
  const [run, setRun] = useState<{ state: "idle" | "running" | "done"; exec?: Execution; error?: string }>({ state: "idle" });
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const completions: Completion[] = useMemo(() => [
    { label: "System.log", detail: "écrit dans le journal" }, { label: "System.warn" }, { label: "System.error" }, { label: "inputs", detail: "entrées du workflow" }, { label: "vars", detail: "variables du workflow" },
    ...Object.keys(graph.inputs.properties ?? {}).map((k) => ({ label: `inputs.${k}`, detail: "entrée du workflow" })),
    ...(graph.variables ?? []).map((v) => ({ label: `vars.${v.name}`, detail: "variable" })),
    ...bindingNames.map((n) => ({ label: n, detail: "entrée liée" })),
    ...(node.outputs ?? []).map((o) => ({ label: o.name, detail: `sortie (${o.type})` })),
  ], [graph, bindingNames, node.outputs]);

  const test = async () => {
    let bindings: unknown, inputs: unknown;
    try { bindings = JSON.parse(sampleBindings || "{}"); inputs = JSON.parse(sampleInputs || "{}"); } catch { setSection("test"); setRun({ state: "done", error: "Les valeurs d'essai ne sont pas du JSON valide." }); setTab("resultat"); return; }
    setSection("test"); setRun({ state: "running" }); setTab("journal");
    try {
      const vars = Object.fromEntries((graph.variables ?? []).map((v) => [v.name, v.value]));
      const start = await api<{ execution_id: string }>(`/workspaces/${workspace.slug}/workflows/${slug}/test-script/`, {
        json: { node: { language: node.language, script: node.script, outputs: node.outputs ?? [], timeout_seconds: node.timeout_seconds ?? 60, bindings }, inputs, vars },
      });
      const end = Date.now() + 150_000;
      while (alive.current && Date.now() < end) {
        const ex = await api<Execution>(`/workspaces/${workspace.slug}/executions/${start.execution_id}/`);
        if (["succeeded", "failed", "cancelled"].includes(ex.status)) { setRun({ state: "done", exec: ex }); setTab(ex.status === "succeeded" ? "resultat" : "journal"); return; }
        setRun({ state: "running", exec: ex });
        await new Promise((r) => setTimeout(r, 700));
      }
      setRun({ state: "done", error: "Le test n'a pas terminé dans le délai imparti." });
    } catch (e) {
      const err = e as ApiError;
      const issues = (err.details as { issues?: { message: string }[] } | null)?.issues;
      setRun({ state: "done", error: issues ? issues.map((i) => i.message).join(" ") : err.message });
      setTab("resultat");
    }
  };

  const logs = run.exec?.attempts?.map((a) => a.logs).filter(Boolean).join("\n") ?? "";
  const typeProblems = run.exec?.status === "succeeded" ? (node.outputs ?? []).filter((o) => !(o.name in (run.exec?.result ?? {})) || !TYPE_CHECK[o.type]?.((run.exec?.result ?? {})[o.name])).map((o) => o.name) : [];

  return (
    <div className="modal-backdrop">
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="sed-title" tabIndex={-1} className="script-dialog">
        <header className="sd-head">
          <h2 id="sed-title">Éditeur de script — {node.name}</h2>
          <div className="sd-tools">
            <label className="inline-label" htmlFor="sd-lang">Langage
              <select id="sd-lang" value={node.language ?? "python"} disabled={!editable} onChange={(e) => onUpdate({ language: e.target.value as ScriptLanguage })}>
                <option value="python">Python</option><option value="javascript">JavaScript</option>
              </select></label>
            <label className="inline-label" htmlFor="sd-to">Délai (s)
              <input id="sd-to" type="number" min={1} max={600} value={node.timeout_seconds ?? 60} disabled={!editable} onChange={(e) => onUpdate({ timeout_seconds: Number(e.target.value) })} /></label>
            <button type="button" className="primary" onClick={() => void test()} disabled={!editable || run.state === "running"}>{run.state === "running" ? "Test en cours…" : "▶ Tester le script"}</button>
            <button type="button" onClick={onClose}>Fermer l'éditeur</button>
          </div>
        </header>
        {node.ai_review && (
          <div role="note" className="notice sd-review">
            <p><strong>🤖 Code écrit par l'IA, non relu.</strong> Lisez-le, vérifiez ce qu'il fait des entrées, testez-le (onglet Test), puis confirmez. Tant que ce n'est pas fait, le workflow ne peut pas être publié.</p>
            <button type="button" className="primary" disabled={!editable} onClick={() => onUpdate({ ai_review: false })}>J'ai relu ce code</button>
          </div>
        )}
        <Tabs label="Sections de l'éditeur" active={section} onChange={(id) => setSection(id as Section)} tabs={[
          { id: "code", label: "Code", content: (
            <div className="sd-pane sd-code">
              <CodeEditor value={node.script ?? ""} onChange={(v) => onUpdate({ script: v })} language={node.language ?? "python"} completions={completions} readOnly={!editable}
                ariaLabel={`Code du script ${node.name} (${node.language === "javascript" ? "JavaScript" : "Python"})`} minHeight="52vh" />
              <p className="hint sd-avail">Disponibles : <code>System.log("…")</code> · <code>inputs</code> · <code>vars</code>{bindingNames.map((n) => <span key={n}> · <code>{n}</code></span>)}</p>
            </div>
          ) },
          { id: "io", label: "Entrées et sorties", content: (
            <div className="sd-pane sd-vars" aria-label="Variables du script">
              <fieldset disabled={!editable} className="bare">
                <BindingsEditor node={node} variables={variables} onUpdate={onUpdate} idPrefix="sdb" />
                <OutputsEditor node={node} onUpdate={onUpdate} idPrefix="sdo" />
              </fieldset>
              <p className="hint">Le script s'exécute dans un conteneur isolé : pas de réseau, utilisateur non privilégié, système de fichiers en lecture seule.</p>
            </div>
          ) },
          { id: "test", label: "Test", content: (
            <section className="sd-pane sd-console" aria-label="Console de test">
              <div role="tablist" aria-label="Console" className="tabs small-tabs">
                {([["essai", "Valeurs d'essai"], ["journal", "Journal"], ["resultat", "Résultat"]] as [ConsoleTab, string][]).map(([id, label]) => (
                  <button key={id} role="tab" type="button" aria-selected={tab === id} onClick={() => setTab(id)}>{label}</button>
                ))}
              </div>
              {tab === "essai" && (
                <div className="sd-samples">
                  <div className="field"><label htmlFor="sd-sb">Valeurs des entrées liées (JSON)</label><textarea id="sd-sb" rows={6} className="mono" value={sampleBindings} onChange={(e) => setSampleBindings(e.target.value)} /></div>
                  <div className="field"><label htmlFor="sd-si">Entrées du workflow (JSON)</label><textarea id="sd-si" rows={6} className="mono" value={sampleInputs} onChange={(e) => setSampleInputs(e.target.value)} /></div>
                  <p className="hint">Le test s'exécute en isolation, dans l'environnement de recette, avec ces valeurs : il n'enchaîne pas le reste du workflow.</p>
                </div>
              )}
              {tab === "journal" && <pre tabIndex={0} aria-label="Journal du test" role="log">{run.state === "idle" ? "Lancez un test pour voir le journal." : logs || (run.state === "running" ? "En cours…" : "(journal vide)")}</pre>}
              {tab === "resultat" && (
                <div role="status" aria-live="polite">
                  {run.state === "idle" && <p>Aucun test lancé.</p>}
                  {run.error && <p className="field-error">Erreur : {run.error}</p>}
                  {run.exec?.status === "succeeded" && <>
                    <p><strong>✔ Le script s'est terminé avec succès.</strong></p>
                    {typeProblems.length > 0 && <p className="field-error">Attention : sortie(s) absente(s) ou de mauvais type : {typeProblems.join(", ")}.</p>}
                    <pre tabIndex={0}>{JSON.stringify(run.exec.result, null, 2)}</pre></>}
                  {run.exec && run.exec.status !== "succeeded" && run.exec.error && <p className="field-error"><strong>✖ {run.exec.error.message}</strong> {run.exec.error.next_step}</p>}
                </div>
              )}
            </section>
          ) },
        ]} />
      </div>
    </div>
  );
}
