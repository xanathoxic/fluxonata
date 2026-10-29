import { useWsQuery } from "../lib";
import { useDraft } from "./DraftContext";
import type { GraphNode } from "./types";
import { HelpTip } from "../components/HelpTip";

interface Providers { results: { name: string; enabled: boolean }[] }

/** A bounded agent: a model chooses, one at a time, among the actions the person allows. */
export function AgentConfig({ node, onUpdate }: { node: GraphNode; onUpdate: (patch: Partial<GraphNode>) => void }) {
  const { actions } = useDraft();
  const providers = (useWsQuery<Providers>("aiproviders", "/ai/providers/").data?.results ?? []).filter((p) => p.enabled);
  const tools = node.tools ?? [];
  const toggle = (slug: string) => onUpdate({ tools: tools.includes(slug) ? tools.filter((t) => t !== slug) : [...tools, slug].slice(0, 10) });
  return (
    <>
      <div className="field">
<HelpTip>Les valeurs {"{{ … }}"} sont remplacées avant d'être transmises. L'agent n'a que cet objectif et les résultats de ses outils : donnez-lui ce qu'il lui faut.</HelpTip><label htmlFor="ag-goal">Objectif</label>
        <textarea id="ag-goal" rows={5} value={node.goal ?? ""} placeholder="Ex. Vérifie l'état du service {{ inputs.service }} et, s'il est arrêté, redémarre-le." onChange={(e) => onUpdate({ goal: e.target.value })} /></div>
      <fieldset className="bare"><legend>Outils autorisés (actions du catalogue, 10 au plus)</legend>
        {actions.filter((a) => a.latest_version).map((a) => (
          <div key={a.slug} className="field field-inline"><input id={`ag-t-${a.slug}`} type="checkbox" checked={tools.includes(a.slug)} onChange={() => toggle(a.slug)} />
            <label htmlFor={`ag-t-${a.slug}`}>{a.name}{a.latest_version?.side_effects && a.latest_version.side_effects !== "none" ? " ⚠ effet externe" : ""}</label></div>))}
        <p className="hint">L'agent ne peut appeler que ces actions, avec leurs entrées validées par leur contrat : il n'a ni accès réseau, ni secret, ni code libre.</p></fieldset>
      <div className="field"><label htmlFor="ag-prov">Fournisseur IA</label>
        <select id="ag-prov" value={node.provider ?? ""} onChange={(e) => onUpdate({ provider: e.target.value })}><option value="">Par défaut</option>{providers.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}</select></div>
      <div className="field"><label htmlFor="ag-max">Nombre maximal de tours (1 à 10)</label>
        <input id="ag-max" type="number" min={1} max={10} value={node.max_steps ?? 5} onChange={(e) => onUpdate({ max_steps: Number(e.target.value) })} /></div>
      <div className="field"><label htmlFor="ag-apr">Validation humaine</label>
        <select id="ag-apr" value={node.approval ?? "effectful"} onChange={(e) => onUpdate({ approval: e.target.value as "never" | "effectful" | "always" })}>
          <option value="effectful">Avant tout outil à effet externe (recommandé)</option><option value="always">Avant chaque outil</option><option value="never">Jamais</option></select></div>
      <div className="field">
<HelpTip>Sorties : <code>steps.{node.id}.output.answer</code> (réponse finale), <code>.steps</code> (outils appelés), <code>.iterations</code>, <code>.stopped</code> (done, max_steps).</HelpTip><label htmlFor="ag-mode">Mode</label>
        <select id="ag-mode" value={node.mode ?? "test"} onChange={(e) => onUpdate({ mode: e.target.value as "test" | "reel" })}>
          <option value="test">Test : le modèle décide, les outils sont simulés (rien n'est exécuté)</option><option value="reel">Réel : les outils sont exécutés</option></select></div>
    </>
  );
}
