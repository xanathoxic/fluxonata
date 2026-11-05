import { useState } from "react";
import type { Action, WorkflowSummary } from "../api";
import { Tabs } from "../components/Tabs";
import { AiConfig } from "./AiConfig";
import { CommentsTab } from "./CommentsTab";
import { AgentConfig } from "./AgentConfig";
import { DataTab } from "./DataTab";
import { ExprPreview } from "./ExprPreview";
import { HttpConfig } from "./HttpConfig";
import { LdapConfig } from "./LdapConfig";
import { SftpConfig } from "./SftpConfig";
import { SmtpConfig } from "./SmtpConfig";
import { SqlConfig } from "./SqlConfig";
import { TransformConfig } from "./TransformConfig";
import { WaitFormFields } from "./WaitFormFields";
import { availableVariables, wouldCreateCycle } from "./graphOps";
import { BrandIcon } from "../components/BrandIcon";
import { RowIcon } from "../components/RowIcon";
import { NodeIcon } from "./icons";
import { ParamEditor } from "./ParamEditor";
import { BindingsEditor, OutputsEditor } from "./ScriptParts";
import { edgeText, NODE_LABELS, type EdgeLabel, type Graph, type GraphNode, type Issue, type SwitchCase } from "./types";
import { HelpTip } from "../components/HelpTip";

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
  onClose?: () => void;
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
  const labelsFor = (): EdgeLabel[] => (node.type === "condition" ? ["true", "false"] : node.type === "switch" ? [...(node.cases ?? []).map((c) => c.id), "default"] : ["join", "race", "wait", "throw"].includes(node.type) ? ["next"] : ["next", "error"]);
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
                  {labelsFor().map((l) => <option key={l} value={l}>{edgeText(l)}</option>)}
                </select>
                <button type="button" className="icon-btn danger-icon" title="Retirer la connexion" onClick={() => p.onDisconnect(e.to)} aria-label={`Supprimer la connexion vers ${target?.name}`}><RowIcon name="close" /></button>
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
    </>
  );

  const exceptions = (
    <>
      {retryable ? (
        <>
          <div className="field">
<HelpTip>Les actions à effet externe non idempotentes ne sont jamais relancées automatiquement.</HelpTip><label htmlFor="retry-max">Nombre maximal de tentatives</label>
            <input id="retry-max" type="number" min={1} max={10} value={node.retry?.max_attempts ?? 1}
              onChange={(e) => onUpdate({ retry: { max_attempts: Number(e.target.value), backoff_seconds: node.retry?.backoff_seconds ?? 2, jitter: node.retry?.jitter ?? 1 } })} /></div>
          <div className="field"><label htmlFor="retry-delay">Délai initial entre tentatives (secondes)</label>
            <input id="retry-delay" type="number" min={0} value={node.retry?.backoff_seconds ?? 2}
              onChange={(e) => onUpdate({ retry: { max_attempts: node.retry?.max_attempts ?? 1, backoff_seconds: Number(e.target.value), jitter: node.retry?.jitter ?? 1 } })} /></div>
        </>
      ) : <p className="hint">Cette étape n'a pas de nouvelle tentative automatique.</p>}
      <p className="help-row"><HelpTip caption="À savoir">Pour traiter l'échec dans le workflow, ajoutez une connexion « En cas d'erreur » (onglet Général) vers l'étape de traitement.</HelpTip></p>
      {node.type === "action" && (
        <>
          <div className="field">
<HelpTip>La compensation est une action métier qui peut elle-même échouer ; son résultat est affiché.</HelpTip><label htmlFor="comp-action">Compensation (annuler cette étape si la suite échoue)</label>
            <select id="comp-action" value={node.compensation?.action ?? ""} onChange={(e) => onUpdate({ compensation: e.target.value ? { action: e.target.value, inputs: {} } : undefined })}>
              <option value="">Aucune</option>{actions.map((a) => <option key={a.slug} value={a.slug}>{a.name}</option>)}
            </select></div>
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
          <p className="help-row"><HelpTip caption="À savoir">Astuce : double-cliquez sur l'étape dans le schéma pour ouvrir l'éditeur.</HelpTip></p>
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
<HelpTip>Opérateurs : + - * / % == != &lt; &lt;= &gt; &gt;= and or not in contains. Fonctions : len, lower, upper, trim, str, number, round, abs, min, max, sum, join, split, replace, starts_with, ends_with, first, last, keys, unique, sort, date_add, date_diff, date_format, default(valeur, repli). Racines : inputs, vars, steps, run.</HelpTip>
          <label htmlFor="expr">Condition</label>
          <input id="expr" list="expr-paths" value={node.expr ?? ""} placeholder="ex. steps.compter.output.words > 3" onChange={(e) => onUpdate({ expr: e.target.value })} />
          <ExprPreview expr={node.expr ?? ""} />
          <label htmlFor="expr-var" className="sr-only">Insérer une variable dans la condition</label>
          <select id="expr-var" value="" onChange={(e) => e.target.value && onUpdate({ expr: `${node.expr ?? ""}${node.expr ? " " : ""}${e.target.value}` })}>
            <option value="">Insérer une variable…</option>{variables.map((v) => <option key={v.path} value={v.path}>{v.label}</option>)}
          </select>
        </div>
      ),
    });
  } else if (node.type === "switch") {
    const cases = node.cases ?? [];
    const setCase = (i: number, patch: Partial<SwitchCase>) => onUpdate({ cases: cases.map((c, k) => (k === i ? { ...c, ...patch } : c)) });
    tabs.push({
      id: "config", label: "Cas",
      content: (
        <div className="field">
          <p className="help-row"><HelpTip caption="À savoir">Les cas sont examinés dans l'ordre : le premier dont la condition est vraie est pris, sinon la sortie « Sinon ». Chaque cas a sa propre sortie à relier.</HelpTip></p>
          {cases.map((c, i) => (
            <fieldset key={i} className="case-row">
              <legend>Cas {i + 1}</legend>
              <label htmlFor={`case-id-${i}`}>Identifiant du cas {i + 1}</label>
              <input id={`case-id-${i}`} value={c.id} disabled={!editable} pattern="[a-z][a-z0-9_]*" onChange={(e) => setCase(i, { id: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })} />
              <label htmlFor={`case-expr-${i}`}>Condition du cas {i + 1}</label>
              <input id={`case-expr-${i}`} list="expr-paths" value={c.expr} disabled={!editable} placeholder="ex. inputs.env == 'prod'" onChange={(e) => setCase(i, { expr: e.target.value })} />
              <ExprPreview expr={c.expr} />
              {editable && <button type="button" className="btn-link" aria-label={`Retirer le cas ${i + 1}`} onClick={() => onUpdate({ cases: cases.filter((_, k) => k !== i) })}>Retirer ce cas</button>}
            </fieldset>
          ))}
          {editable && cases.length < 20 && <button type="button" className="btn" onClick={() => onUpdate({ cases: [...cases, { id: `cas_${cases.length + 1}`, expr: "" }] })}>Ajouter un cas</button>}
          <p className="help-row"><HelpTip caption="À savoir">Opérateurs : + - * / % == != &lt; &lt;= &gt; &gt;= and or not in contains. Fonctions : len, lower, upper, trim, str, number, round, abs, min, max, sum, join, split, replace, starts_with, ends_with, first, last, keys, unique, sort, date_add, date_diff, date_format, default(valeur, repli). Racines : inputs, vars, steps, run.</HelpTip></p>
        </div>
      ),
    });
  } else if (node.type === "wait") {
    tabs.push({ id: "config", label: "Minuterie", content: (
      <>
        <div className="field"><label htmlFor="wait-r">Reprise</label>
          <select id="wait-r" value={node.resume ?? "timer"} onChange={(e) => onUpdate({ resume: e.target.value === "webhook" ? "webhook" : e.target.value === "form" ? "form" : "timer", ...(e.target.value === "form" && !node.form ? { form: { properties: { reponse: { type: "string", title: "Réponse" } }, required: ["reponse"] } } : {}) })}>
            <option value="timer">À l'échéance du délai</option>
            <option value="webhook">Sur appel HTTP (ou à l'échéance)</option>
            <option value="form">Sur formulaire rempli par une personne (ou à l'échéance)</option>
          </select></div>
        <div className="field">
<HelpTip>L'attente est durable : elle survit à un redémarrage et ne mobilise aucun worker.</HelpTip><label htmlFor="wait-s">{node.resume === "webhook" || node.resume === "form" ? "Délai maximum (secondes)" : "Durée d'attente (secondes)"}</label>
          <input id="wait-s" type="number" min={1} value={node.seconds ?? 60} onChange={(e) => onUpdate({ seconds: Number(e.target.value) })} /></div>
        {node.resume === "form" && <><WaitFormFields node={node} onUpdate={onUpdate} /><p className="hint">Une page publique <code>/r/…</code> est créée à chaque exécution (adresse visible sur la page de l'exécution, pour les personnes qui peuvent lancer) : la personne qui la reçoit remplit le formulaire, une seule fois. Sortie : <code>resumed</code> et <code>payload</code>.</p></>}
        {node.resume === "webhook" && <p className="hint">Une adresse de reprise est créée à chaque exécution (visible sur la page de l'exécution, pour les personnes qui peuvent lancer). Un appel POST JSON la reprend ; la sortie contient <code>resumed</code> (faux si le délai est écoulé) et <code>payload</code> (le corps reçu : <code>steps.{node.id}.output.payload.champ</code>).</p>}
      </>) });
  } else if (node.type === "transform") {
    tabs.push({ id: "config", label: "Transformation", content: <TransformConfig node={node} onUpdate={onUpdate} /> });
  } else if (node.type === "agent") {
    tabs.push({ id: "config", label: "Agent", content: <AgentConfig node={node} onUpdate={onUpdate} /> });
  } else if (node.type === "smtp") {
    tabs.push({ id: "config", label: "E-mail", content: <SmtpConfig node={node} onUpdate={onUpdate} /> });
  } else if (node.type === "sftp") {
    tabs.push({ id: "config", label: "Fichier SFTP", content: <SftpConfig node={node} onUpdate={onUpdate} /> });
  } else if (node.type === "ldap") {
    tabs.push({ id: "config", label: "Annuaire", content: <LdapConfig node={node} onUpdate={onUpdate} /> });
  } else if (node.type === "sql") {
    tabs.push({ id: "config", label: "Requête SQL", content: <SqlConfig node={node} onUpdate={onUpdate} /> });
  } else if (node.type === "http") {
    tabs.push({ id: "config", label: "Requête", content: <HttpConfig node={node} onUpdate={onUpdate} /> });
  } else if (node.type === "throw") {
    tabs.push({ id: "config", label: "Exception", content: (
      <>
        <div className="field"><label htmlFor="th-code">Code d'erreur</label><input id="th-code" value={node.code ?? ""} pattern="[A-Z0-9_]*" onChange={(e) => onUpdate({ code: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_") })} /></div>
        <div className="field"><label htmlFor="th-msg">Message affiché à l'utilisateur</label><input id="th-msg" value={node.message ?? ""} onChange={(e) => onUpdate({ message: e.target.value })} />
          <label htmlFor="th-var" className="sr-only">Insérer une variable dans le message</label>
          <select id="th-var" value="" onChange={(e) => e.target.value && onUpdate({ message: `${node.message ?? ""} {{ ${e.target.value} }}` })}><option value="">Insérer une variable…</option>{variables.map((v) => <option key={v.path} value={v.path}>{v.label}</option>)}</select></div>
        <p className="help-row"><HelpTip caption="À savoir">Arrête le workflow en échec avec ce message (sauf si une connexion « En cas d'erreur » existe).</HelpTip></p>
      </>) });
  } else if (node.type === "approval") {
    tabs.push({ id: "config", label: "Interaction", content: (
      <>
        <div className="field"><label htmlFor="ap-title">Ce qui doit être approuvé</label><input id="ap-title" value={node.title ?? ""} onChange={(e) => onUpdate({ title: e.target.value })} /></div>
        <div className="field"><label htmlFor="ap-ins">Consignes pour l'approbateur</label><textarea id="ap-ins" rows={3} value={node.instructions ?? ""} onChange={(e) => onUpdate({ instructions: e.target.value })} /></div>
        <div className="field field-inline"><input id="ap-self" type="checkbox" checked={node.allow_self_approval ?? false} onChange={(e) => onUpdate({ allow_self_approval: e.target.checked })} /><label htmlFor="ap-self">Autoriser l'auto-approbation (déconseillé)</label></div>
      </>) });
  } else if (node.type === "subworkflow" || node.type === "foreach" || node.type === "while") {
    tabs.push({ id: "config", label: node.type === "foreach" || node.type === "while" ? "Boucle" : "Workflow", content: (
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
        {node.type === "while" && (
          <>
            <div className="field">
<HelpTip>Évaluée après chaque répétition (le workflow est toujours exécuté au moins une fois). <code>last</code> est le résultat de la répétition qui vient de finir (ses sorties), <code>iteration</code> le nombre de répétitions faites. Dans les entrées, <code>{"{{ default(last.champ, 0) }}"}</code> lit le résultat précédent (absent à la première répétition).</HelpTip><label htmlFor="wh-cond">Continuer tant que</label><input id="wh-cond" list="expr-paths" value={node.condition ?? ""} placeholder="ex. last.statut != 'termine'" onChange={(e) => onUpdate({ condition: e.target.value })} />
              <ExprPreview expr={node.condition ?? ""} /></div>
            <div className="field"><label htmlFor="wh-max">Nombre maximal de répétitions (1 à 100)</label><input id="wh-max" type="number" min={1} max={100} value={node.max_iterations ?? 10} onChange={(e) => onUpdate({ max_iterations: Number(e.target.value) })} /></div>
            <div className="field">
<HelpTip>La pause est durable (aucun travailleur n'est occupé). Sorties : <code>steps.{node.id}.output.iterations</code>, <code>.last</code>, <code>.results</code>, <code>.stopped</code> (condition ou max_iterations).</HelpTip><label htmlFor="wh-delay">Pause entre deux répétitions (secondes, 0 à 86400)</label><input id="wh-delay" type="number" min={0} max={86400} value={node.delay_seconds ?? 0} onChange={(e) => onUpdate({ delay_seconds: Number(e.target.value) })} /></div>
          </>
        )}
        {node.type === "foreach" && (
          <>
            <div className="field"><label htmlFor="fe-items">Liste à parcourir</label><input id="fe-items" list="expr-paths" value={node.items ?? ""} placeholder="ex. inputs.liste" onChange={(e) => onUpdate({ items: e.target.value })} />
              <ExprPreview expr={node.items ?? ""} />
              <label htmlFor="fe-var" className="sr-only">Insérer une variable liste</label>
              <select id="fe-var" value="" onChange={(e) => e.target.value && onUpdate({ items: e.target.value })}><option value="">Utiliser une variable…</option>{variables.filter((v) => !v.type || v.type === "array").map((v) => <option key={v.path} value={v.path}>{v.label}</option>)}</select></div>
            <div className="field"><label htmlFor="fe-item">Entrée du workflow qui reçoit l'élément</label><input id="fe-item" value={node.item_input ?? "item"} onChange={(e) => onUpdate({ item_input: e.target.value })} /></div>
            <div className="field">
<HelpTip>Les boucles sont toujours bornées.</HelpTip><label htmlFor="fe-max">Nombre maximal d'itérations (1 à 100)</label><input id="fe-max" type="number" min={1} max={100} value={node.max_iterations ?? 10} onChange={(e) => onUpdate({ max_iterations: Number(e.target.value) })} /></div>
            <div className="field"><label htmlFor="fe-conc">Itérations en parallèle</label><input id="fe-conc" type="number" min={1} max={10} value={node.concurrency ?? 1} onChange={(e) => onUpdate({ concurrency: Number(e.target.value) })} /></div>
            <div className="field"><label htmlFor="fe-int">Cadence : une itération au plus toutes les (secondes, 0 = sans limite)</label><input id="fe-int" type="number" min={0} max={3600} value={node.interval_seconds ?? 0} onChange={(e) => onUpdate({ interval_seconds: Number(e.target.value) })} /></div>
          </>
        )}
      </>) });
  } else if (node.type === "race") {
    tabs.push({ id: "config", label: "Premier arrivé", content: (
      <>
        <div className="field">
<HelpTip>L'étape continue dès que <strong>la première</strong> branche reliée à elle est terminée. Sorties : <code>steps.{node.id}.output.winner</code> (identifiant de la branche arrivée), <code>.winner_name</code> et <code>.value</code> (son résultat). Les étapes des autres branches ne sont pas garanties après ce point : lisez le gagnant. Un effet déjà produit par une branche annulée n'est pas défait. Cas d'usage : une demande de validation contre une minuterie (délai de réponse), ou le service le plus rapide.</HelpTip><label htmlFor="race-losers">Branches plus lentes</label>
          <select id="race-losers" value={node.losers ?? "cancel"} onChange={(e) => onUpdate({ losers: e.target.value === "finish" ? "finish" : "cancel" })}>
            <option value="cancel">Annulées dès qu'une branche est arrivée</option>
            <option value="finish">Laissées terminer (leur résultat n'est pas lu)</option>
          </select></div>
      </>
    ) });
  } else if (node.type === "ai") {
    tabs.push({ id: "config", label: "Étape IA", content: <AiConfig node={node} variables={variables} editable={editable} onUpdate={onUpdate} /> });
  }
  if (node.type !== "join" && node.type !== "race" && node.type !== "wait" && node.type !== "throw" && node.type !== "condition" && node.type !== "switch" && node.type !== "approval" && node.type !== "foreach" && node.type !== "subworkflow" && node.type !== "http" && node.type !== "sql" && node.type !== "agent" && node.type !== "ldap" && node.type !== "sftp" && node.type !== "smtp" && node.type !== "transform") {
    tabs.push({ id: "exceptions", label: "Exceptions", content: exceptions });
  } else if (retryable) {
    tabs.push({ id: "exceptions", label: "Exceptions", content: exceptions });
  }
  if (node.type !== "join") tabs.push({ id: "data", label: "Données", content: <DataTab node={node} /> });
  tabs.push({ id: "comments", label: "Commentaires", content: <CommentsTab node={node} /> });
  const active = tabs.some((t) => t.id === tab) ? tab : "general";
  return (
    <section aria-label={`Configuration de l'étape ${node.name}`} className="node-panel">
      <datalist id="expr-paths">{variables.map((v) => <option key={v.path} value={v.path}>{v.label}</option>)}</datalist>
      <header className="np-head">
        <span className="np-ico" aria-hidden="true">{node.integration ? <BrandIcon slug={node.integration} name={node.integration.charAt(0).toUpperCase() + node.integration.slice(1)} size={34} /> : <NodeIcon kind={node.type} size={22} />}</span>
        <div className="np-id"><h2 className="np-title">{node.name || node.id}</h2><p className="np-sub"><span className="adm-badge adm-badge-info">{NODE_LABELS[node.type]}</span> <code>{node.id}</code></p></div>
        <div className="np-tools" role="toolbar" aria-label="Actions sur l'étape">
          <button type="button" className="icon-btn" title="Dupliquer l'étape" aria-label="Dupliquer l'étape" onClick={p.onDuplicate} disabled={!editable}><RowIcon name="copy" /></button>
          <button type="button" className="icon-btn danger-icon" title="Supprimer l'étape" aria-label="Supprimer l'étape" onClick={p.onDelete} disabled={!editable}><RowIcon name="trash" /></button>
          {p.onClose && <button type="button" className="icon-btn" title="Fermer les propriétés" aria-label="Fermer les propriétés" onClick={p.onClose}><RowIcon name="close" /></button>}
        </div>
      </header>
      <fieldset disabled={!editable} className="bare">
        <Tabs label={`Propriétés de ${node.name}`} tabs={tabs} active={active} onChange={setTab} />
      </fieldset>
    </section>
  );
}
