import { useEffect } from "react";
import type { JsonSchema } from "../api";
import { useWsQuery } from "../lib";
import { useSkills } from "../pages/skills/SkillsPage";
import type { OutputField } from "./graphOps";
import { ParamEditor } from "./ParamEditor";
import type { GraphNode } from "./types";
import { HelpTip } from "../components/HelpTip";

/** AI step: either a library skill (versioned, typed inputs, fixed answer format) or a free-form task. */
export function AiConfig({ node, variables, editable, onUpdate }: { node: GraphNode; variables: OutputField[]; editable: boolean; onUpdate: (p: Partial<GraphNode>) => void }) {
  const skillMode = node.skill !== undefined;
  const skills = useSkills();
  const skill = skillMode ? skills.data?.results.find((s) => s.slug === node.skill) : undefined;
  const sv = skill?.latest_version ?? null;
  // The answer fields feed the variable pickers of later steps: keep them in sync with the skill's answer format.
  const answer = sv ? JSON.stringify(sv.output_schema) : null;
  useEffect(() => {
    if (editable && sv && answer !== JSON.stringify(node.output_schema)) onUpdate({ output_schema: sv.output_schema });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the skill's answer format changes
  }, [answer, editable]);
  const prov = useWsQuery<{ results: { name: string; cloud: boolean }[] }>("aiproviders-pick", "/ai/providers/");
  const props = Object.entries(node.output_schema?.properties ?? {});
  const write = (rows: [string, JsonSchema][]) =>
    onUpdate({ output_schema: { type: "object", required: rows.map(([k]) => k), properties: Object.fromEntries(rows.map(([k, v]) => [k, { type: (Array.isArray(v.type) ? v.type[0] : v.type) ?? "string", title: v.title ?? k }])) } });
  const mode = (
    <fieldset>
      <legend>Que doit faire cette étape ?</legend>
      <div className="field field-inline"><input type="radio" id="ai-m-skill" name="ai-mode" checked={skillMode} onChange={() => onUpdate({ skill: "", skill_version: undefined, prompt: undefined })} /><label htmlFor="ai-m-skill">Utiliser un skill de la bibliothèque</label></div>
      <div className="field field-inline"><input type="radio" id="ai-m-free" name="ai-mode" checked={!skillMode} onChange={() => onUpdate({ skill: undefined, skill_version: undefined, inputs: undefined, prompt: node.prompt ?? "" })} /><label htmlFor="ai-m-free">Une tâche libre</label></div>
    </fieldset>
  );
  if (skillMode) {
    const pick = (slug: string) => { const sk = skills.data?.results.find((x) => x.slug === slug); onUpdate({ skill: slug, skill_version: undefined, inputs: {}, ...(sk?.latest_version ? { output_schema: sk.latest_version.output_schema } : {}) }); };
    const schema = sv?.input_schema;
    return (
      <>
        {mode}
        <p className="help-row"><HelpTip caption="À savoir">L'étape IA n'a accès à aucun outil. Sa réponse est une donnée non fiable : elle doit respecter le format défini par le skill, sinon l'étape échoue.</HelpTip></p>
        <div className="field"><label htmlFor="ai-sk">Skill</label>
          <select id="ai-sk" value={node.skill ?? ""} onChange={(e) => pick(e.target.value)}>
            <option value="">Choisir…</option>
            {skills.data?.results.filter((x) => x.latest_version && (x.status === "active" || x.slug === node.skill)).map((x) => <option key={x.slug} value={x.slug}>{x.name}{x.status === "deprecated" ? " (déprécié)" : ""}{x.folder ? ` — ${x.folder}` : ""}</option>)}
          </select>
          {skills.data?.results.length === 0 && <p className="hint">Aucun skill pour l'instant : créez-en dans Bibliothèques → Skills IA.</p>}
        </div>
        {sv && skill && (
          <>
            <p className="hint">{skill.description} Version utilisée : la dernière publiée ({sv.version}), figée à la publication du workflow.</p>
            <div className="field"><label htmlFor="ai-p">Fournisseur d'IA</label>
              <select id="ai-p" value={node.provider ?? ""} onChange={(e) => onUpdate({ provider: e.target.value })}>
                <option value="">{sv.default_provider ? `Par défaut du skill (${sv.default_provider})` : "Choisir…"}</option>
                {prov.data?.results.map((p) => <option key={p.name} value={p.name}>{p.name} {p.cloud ? "(cloud)" : "(local)"}</option>)}
              </select></div>
            {schema && Object.entries(schema.properties ?? {}).map(([k, pr]) => (
              <ParamEditor key={k} name={k} schema={pr} required={schema.required?.includes(k) ?? false} value={node.inputs?.[k]} variables={variables} onChange={(v) => onUpdate({ inputs: { ...node.inputs, [k]: v } })} />
            ))}
            <details><summary>Voir les instructions du skill</summary><pre tabIndex={0} className="wrap" aria-label="Instructions du skill">{sv.instructions}</pre></details>
          </>
        )}
      </>
    );
  }
  return (
    <>
      {mode}
      <p className="help-row"><HelpTip caption="À savoir">L'étape IA n'a accès à aucun outil. Sa réponse est considérée comme une donnée non fiable : elle doit respecter le format ci-dessous, sinon l'étape échoue.</HelpTip></p>
      <div className="field"><label htmlFor="ai-p">Fournisseur d'IA</label>
        <select id="ai-p" value={node.provider ?? ""} onChange={(e) => onUpdate({ provider: e.target.value })}><option value="">Choisir…</option>{prov.data?.results.map((p) => <option key={p.name} value={p.name}>{p.name} {p.cloud ? "(cloud)" : "(local)"}</option>)}</select></div>
      <div className="field"><label htmlFor="ai-t">Tâche confiée à l'IA</label><textarea id="ai-t" rows={4} value={node.prompt ?? ""} onChange={(e) => onUpdate({ prompt: e.target.value })} />
        <label htmlFor="ai-v" className="sr-only">Insérer une variable dans la tâche</label>
        <select id="ai-v" value="" onChange={(e) => e.target.value && onUpdate({ prompt: `${node.prompt ?? ""} {{ ${e.target.value} }}` })}><option value="">Insérer une variable…</option>{variables.map((v) => <option key={v.path} value={v.path}>{v.label}</option>)}</select></div>
      <fieldset><legend>Champs de la réponse attendue</legend>
        {props.map(([k, v], i) => (
          <div className="edge-row" key={i}>
            <label className="sr-only" htmlFor={`ao-k-${i}`}>Nom du champ {i + 1}</label><input id={`ao-k-${i}`} value={k} pattern="[a-z][a-z0-9_]*" onChange={(e) => write(props.map(([kk, vv], j) => (j === i ? [e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_"), vv] : [kk, vv])))} />
            <label className="sr-only" htmlFor={`ao-t-${i}`}>Type du champ {i + 1}</label><select id={`ao-t-${i}`} value={String(v.type ?? "string")} onChange={(e) => write(props.map(([kk, vv], j) => (j === i ? [kk, { ...vv, type: e.target.value }] : [kk, vv])))}><option value="string">Texte</option><option value="integer">Entier</option><option value="number">Nombre</option><option value="boolean">Oui / Non</option></select>
            <button type="button" onClick={() => write(props.filter((_, j) => j !== i))} disabled={props.length < 2}>Retirer</button>
          </div>
        ))}
        <button type="button" onClick={() => write([...props, [`champ_${props.length + 1}`, { type: "string" } as JsonSchema]])}>Ajouter un champ</button>
      </fieldset>
    </>
  );
}
