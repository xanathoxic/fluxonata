import { useState } from "react";
import type { Action, WorkflowSummary } from "../api";
import { Tabs } from "../components/Tabs";
import { AiConfig } from "./AiConfig";
import { availableVariables, wouldCreateCycle } from "./graphOps";
import { NodeIcon } from "./icons";
import { ParamEditor } from "./ParamEditor";
import { BindingsEditor, OutputsEditor } from "./ScriptParts";
import { EDGE_LABELS, NODE_LABELS, type EdgeLabel, type Graph, type GraphNode, type Issue } from "./types";

interface Props {
  graph: Graph;
  node: GraphNode;
  actions: Action[];
  workflows: WorkflowSummary[];
  issues: Issue[];
  editable: boolean;
  onUpdate: (patch: Partial<GraphNode>) => void;
  onConnect: (to: string, label: EdgeLabel) => void;
  onDisconnect: (to: string) => void;
  onRelabel: (to: string, label: EdgeLabel) => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onOpenScript: () => void;
}

/** Right-hand properties panel: tabs General / Inputs-Outputs / Script / Exception handling, per element type. */
export function NodePanel(p: Props) {
  const { graph, node, actions, workflows, issues, editable, onUpdate } = p;
  const [tab, setTab] = useState("general");
  const variables = availableVariables(graph, node.id, actions);
  const action = actions.find((a) => a.slug === node.action);
  const schema = action?.latest_version?.input_schema;
  const outgoing = graph.edges.filter((e) => e.from === node.id);
  const targets = graph.nodes.filter((n) => n.id !== node.id && !outgoing.some((e) => e.to === n.id) && !wouldCreateCycle(graph, node.id, n.id));
  const labelsFor = (): EdgeLabel[] => (node.type === "condition" ? ["true", "false"] : ["join", "wait", "throw"].includes(node.type) ? ["next"] : ["next", "error"]);
  const mine = issues.filter((i) => i.node === node.id);
  const retryable = ["action", "script", "ai"].includes(node.type);

  const general = (
    <>
      {mine.length > 0 && (
        <ul className="issue-list" aria-label="Problèmes de cette étape">
          {mine.map((i, k) => <li key={k} className={`issue issue-${i.level}`}><span aria-hidden="true">{i.level === "error" ? "✖" : "⚠"} </span><span className="sr-only">{i.level === "error" ? "Erreur : " : "Avertissement : "}</span>{i.message}</li>)}
        </ul>
      )}
      <div className="field"><label htmlFor="node-name">Nom de l'étape</label><input id="node-name" value={node.name} onChange={(e) => onUpdate({ name: e.target.value })} /></div>
      <div className="field"><label htmlFor="node-desc">Description</label><textarea id="node-desc" rows={2} value={node.description ?? ""} onChange={(e) => onUpdate({ description: e.target.value })} /></div>
      <p className="hint">Identifiant technique : <code>{node.id}</code> · Type : {NODE_LABELS[node.type]}</p>
      <fieldset>
        <legend>Étapes suivantes</legend>
        {outgoing.length === 0 && <p className="hint">Aucune étape après celle-ci : le workflow se termine ici.</p>}
        <ul className="plain">
          {outgoing.map((e) => {
            const target = graph.nodes.find((n) => n.id === e.to);
            return (
              <li key={e.to} className="edge-row">
                <span>→ {target?.name ?? e.to}</span>
                <label className="sr-only" htmlFor={`lab-${e.to}`}>Type de connexion vers {target?.name}</label>
                <select id={`lab-${e.to}`} value={e.label ?? "next"} onChange={(ev) => p.onRelabel(e.to, ev.target.value as EdgeLabel)}>
                  {labelsFor().map((l) => <option key={l} value={l}>{EDGE_LABELS[l]}</option>)}
                </select>
                <button type="button" onClick={() => p.onDisconnect(e.to)} aria-label={`Supprimer la connexion vers ${target?.name}`}>Retirer</button>
              </li>
            );
          })}
        </ul>
        {targets.length > 0 && (
          <div className="edge-row">
            <label htmlFor="add-edge">Connecter à</label>
            <select id="add-edge" value="" onChange={(e) => e.target.value && p.onConnect(e.target.value, labelsFor()[0] === "true" ? (outgoing.some((x) => x.label === "true") ? "false" : "true") : "next")}>
              <option value="">Choisir une étape…</option>
              {targets.map((n) => <option key={n.id} value={n.id}>{n.name}</option>)}
            </select>
          </div>
        )}
      </fieldset>
      <div className="actions">
        <button type="button" onClick={p.onDuplicate}>Dupliquer l'étape</button>
        <button type="button" onClick={p.onDelete} className="danger">Supprimer l'étape</button>
      </div>
    </>
  );

  const exceptions = (
    <>
      {retryable ? (
        <>
          <div className="field"><label htmlFor="retry-max">Nombre maximal de tentatives</label>
            <input id="retry-max" type="number" min={1} max={10} value={node.retry?.max_attempts ?? 1}
              onChange={(e) => onUpdate({ retry: { max_attempts: Number(e.target.value), backoff_seconds: node.retry?.backoff_seconds ?? 2, jitter: node.retry?.jitter ?? 1 } })} />
            <p className="hint">Les actions à effet externe non idempotentes ne sont jamais relancées automatiquement.</p></div>
          <div className="field"><label htmlFor="retry-delay">Délai initial entre tentatives (secondes)</label>
            <input id="retry-delay" type="number" min={0} value={node.retry?.backoff_seconds ?? 2}
              onChange={(e) => onUpdate({ retry: { max_attempts: node.retry?.max_attempts ?? 1, backoff_seconds: Number(e.target.value), jitter: node.retry?.jitter ?? 1 } })} /></div>
        </>
      ) : <p className="hint">Cette étape n'a pas de nouvelle tentative automatique.</p>}
      <p className="hint">Pour traiter l'échec dans le workflow, ajoutez une connexion « En cas d'erreur » (onglet Général) vers l'étape de traitement.</p>
      {node.type === "action" && (
        <>
          <div className="field"><label htmlFor="comp-action">Compensation (annuler cette étape si la suite échoue)</label>
            <select id="comp-action" value={node.compensation?.action ?? ""} onChange={(e) => onUpdate({ compensation: e.target.value ? { action: e.target.value, inputs: {} } : undefined })}>
              <option value="">Aucune</option>{actions.map((a) => <option key={a.slug} value={a.slug}>{a.name}</option>)}
            </select>
            <p className="hint">La compensation est une action métier qui peut elle-même échouer ; son résultat est affiché.</p></div>
          {node.compensation && (() => {
            const cs = actions.find((a) => a.slug === node.compensation?.action)?.latest_version?.input_schema;
            const compVars = [...variables, ...Object.entries(action?.latest_version?.output_schema.properties ?? {}).map(([k, pr]) => ({ path: `step.output.${k}`, label: `Résultat de cette étape → ${pr.title ?? k}`, type: pr.type }))];
            return Object.entries(cs?.properties ?? {}).map(([k, pr]) => (
              <ParamEditor key={k} name={k} schema={pr} required={cs?.required?.includes(k) ?? false} value={node.compensation?.inputs[k]} variables={compVars}
                onChange={(v) => onUpdate({ compensation: { action: node.compensation?.action ?? "", inputs: { ...node.compensation?.inputs, [k]: v } } })} />
            ));
          })()}
        </>
      )}
    </>
  );

  const tabs: { id: string; label: string; content: React.ReactNode }[] = [{ id: "general", label: "Général", content: general }];
  if (node.type === "script") {
    tabs.push({ id: "io", label: "Entrées/Sorties", content: <><BindingsEditor node={node} variables={variables} onUpdate={onUpdate} /><OutputsEditor node={node} onUpdate={onUpdate} /></> });
    tabs.push({
      id: "script", label: "Script",
      content: (
        <>
          <div className="field"><label htmlFor="np-lang">Langage</label>
            <select id="np-lang" value={node.language ?? "python"} onChange={(e) => onUpdate({ language: e.target.value as "python" | "javascript" })}>
              <option value="python">Python</option><option value="javascript">JavaScript</option></select></div>
          <button type="button" className="primary" onClick={p.onOpenScript}>{editable ? "Ouvrir l'éditeur de script" : "Voir le script"}</button>
          <pre tabIndex={0} className="script-preview" aria-label="Aperçu du script">{(node.script ?? "").split("\n").slice(0, 14).join("\n")}</pre>
          <p className="hint">Astuce : double-cliquez sur l'étape dans le schéma pour ouvrir l'éditeur.</p>
        </>
      ),
    });
  } else if (node.type === "action") {
    tabs.push({
      id: "io", label: "Entrées",
      content: (
        <>
          {!action && <p role="alert" className="field-error">Erreur : action introuvable ({node.action}).</p>}
          {schema && Object.entries(schema.properties ?? {}).map(([k, pr]) => (
            <ParamEditor key={k} name={k} schema={pr} required={schema.required?.includes(k) ?? false} value={node.inputs?.[k]} variables={variables} onChange={(v) => onUpdate({ inputs: { ...node.inputs, [k]: v } })} />
          ))}
        </>
      ),
    });
  } else if (node.type === "condition") {
    tabs.push({
      id: "config", label: "Décision",
      content: (
        <div className="field">
          <label htmlFor="expr">Condition</label>
          <input id="expr" value={node.expr ?? ""} placeholder="ex. steps.compter.output.words > 3" onChange={(e) => onUpdate({ expr: e.target.value })} />
          <p className="hint">Opérateurs : == != &lt; &lt;= &gt; &gt;= and or not in contains. Fonctions : len, lower, upper.</p>
          <label htmlFor="expr-var" className="sr-only">Insérer une variable dans la condition</label>
          <select id="expr-var" value="" onChange={(e) => e.target.value && onUpdate({ expr: `${node.expr ?? ""}${node.expr ? " " : ""}${e.target.value}` })}>
            <option value="">Insérer une variable…</option>{variables.map((v) => <option key={v.path} value={v.path}>{v.label}</option>)}
          </select>
        </div>
      ),
    });
  } else if (node.type === "wait") {
    tabs.push({ id: "config", label: "Minuterie", content: (
      <div className="field"><label htmlFor="wait-s">Durée d'attente (secondes)</label>
        <input id="wait-s" type="number" min={1} value={node.seconds ?? 60} onChange={(e) => onUpdate({ seconds: Number(e.target.value) })} />
        <p className="hint">L'attente est durable : elle survit à un redémarrage et ne mobilise aucun worker.</p></div>) });
  } else if (node.type === "throw") {
    tabs.push({ id: "config", label: "Exception", content: (
      <>
        <div className="field"><label htmlFor="th-code">Code d'erreur</label><input id="th-code" value={node.code ?? ""} pattern="[A-Z0-9_]*" onChange={(e) => onUpdate({ code: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_") })} /></div>
        <div className="field"><label htmlFor="th-msg">Message affiché à l'utilisateur</label><input id="th-msg" value={node.message ?? ""} onChange={(e) => onUpdate({ message: e.target.value })} />
          <label htmlFor="th-var" className="sr-only">Insérer une variable dans le message</label>
          <select id="th-var" value="" onChange={(e) => e.target.value && onUpdate({ message: `${node.message ?? ""} {{ ${e.target.value} }}` })}><option value="">Insérer une variable…</option>{variables.map((v) => <option key={v.path} value={v.path}>{v.label}</option>)}</select></div>
        <p className="hint">Arrête le workflow en échec avec ce message (sauf si une connexion « En cas d'erreur » existe).</p>
      </>) });
  } else if (node.type === "approval") {
    tabs.push({ id: "config", label: "Interaction", content: (
      <>
        <div className="field"><label htmlFor="ap-title">Ce qui doit être approuvé</label><input id="ap-title" value={node.title ?? ""} onChange={(e) => onUpdate({ title: e.target.value })} /></div>
        <div className="field"><label htmlFor="ap-ins">Consignes pour l'approbateur</label><textarea id="ap-ins" rows={3} value={node.instructions ?? ""} onChange={(e) => onUpdate({ instructions: e.target.value })} /></div>
        <div className="field field-inline"><input id="ap-self" type="checkbox" checked={node.allow_self_approval ?? false} onChange={(e) => onUpdate({ allow_self_approval: e.target.checked })} /><label htmlFor="ap-self">Autoriser l'auto-approbation (déconseillé)</label></div>
      </>) });
  } else if (node.type === "subworkflow" || node.type === "foreach") {
    tabs.push({ id: "config", label: node.type === "foreach" ? "Boucle" : "Workflow", content: (
      <>
        <div className="field"><label htmlFor="sub-wf">Workflow appelé</label>
          <select id="sub-wf" value={node.workflow ?? ""} onChange={(e) => onUpdate({ workflow: e.target.value })}><option value="">Choisir…</option>
            {workflows.filter((w) => w.latest_version).map((w) => <option key={w.slug} value={w.slug}>{w.name} (v{w.latest_version?.version})</option>)}</select></div>
        {(() => {
          const wf = workflows.find((w) => w.slug === node.workflow);
          const props = wf?.latest_version?.input_schema.properties ?? {};
          return Object.entries(props).filter(([k]) => !(node.type === "foreach" && k === node.item_input)).map(([k, pr]) => (
            <ParamEditor key={k} name={k} schema={pr} required={wf?.latest_version?.input_schema.required?.includes(k) ?? false} value={node.inputs?.[k]} variables={variables} onChange={(v) => onUpdate({ inputs: { ...node.inputs, [k]: v } })} />));
        })()}
        {node.type === "foreach" && (
          <>
            <div className="field"><label htmlFor="fe-items">Liste à parcourir</label><input id="fe-items" value={node.items ?? ""} placeholder="ex. inputs.liste" onChange={(e) => onUpdate({ items: e.target.value })} />
              <label htmlFor="fe-var" className="sr-only">Insérer une variable liste</label>
              <select id="fe-var" value="" onChange={(e) => e.target.value && onUpdate({ items: e.target.value })}><option value="">Utiliser une variable…</option>{variables.filter((v) => !v.type || v.type === "array").map((v) => <option key={v.path} value={v.path}>{v.label}</option>)}</select></div>
            <div className="field"><label htmlFor="fe-item">Entrée du workflow qui reçoit l'élément</label><input id="fe-item" value={node.item_input ?? "item"} onChange={(e) => onUpdate({ item_input: e.target.value })} /></div>
            <div className="field"><label htmlFor="fe-max">Nombre maximal d'itérations (1 à 100)</label><input id="fe-max" type="number" min={1} max={100} value={node.max_iterations ?? 10} onChange={(e) => onUpdate({ max_iterations: Number(e.target.value) })} /><p className="hint">Les boucles sont toujours bornées.</p></div>
            <div className="field"><label htmlFor="fe-conc">Itérations en parallèle</label><input id="fe-conc" type="number" min={1} max={10} value={node.concurrency ?? 1} onChange={(e) => onUpdate({ concurrency: Number(e.target.value) })} /></div>
          </>
        )}
      </>) });
  } else if (node.type === "ai") {
    tabs.push({ id: "config", label: "Étape IA", content: <AiConfig node={node} variables={variables} editable={editable} onUpdate={onUpdate} /> });
  }
  if (node.type !== "join" && node.type !== "wait" && node.type !== "throw" && node.type !== "condition" && node.type !== "approval" && node.type !== "foreach" && node.type !== "subworkflow") {
    tabs.push({ id: "exceptions", label: "Exceptions", content: exceptions });
  } else if (retryable) {
    tabs.push({ id: "exceptions", label: "Exceptions", content: exceptions });
  }
  const active = tabs.some((t) => t.id === tab) ? tab : "general";
  return (
    <section aria-label={`Configuration de l'étape ${node.name}`} className="node-panel">
      <h2 className="np-title"><NodeIcon kind={node.type} size={20} /> <span>{node.id} : {NODE_LABELS[node.type]}</span></h2>
      <fieldset disabled={!editable} className="bare">
        <Tabs label={`Propriétés de ${node.name}`} tabs={tabs} active={active} onChange={setTab} />
      </fieldset>
    </section>
  );
}
