import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type ApiError, type AiSkill } from "../../api";
import { Overlay } from "../../components/Overlay";
import { useApp } from "../../context";
import { errorText, useWsQuery } from "../../lib";
import { FieldsEditor } from "./FieldsEditor";
import { fieldsToSchema, schemaToFields, slugifyName, type Field } from "./fields";

const EXAMPLE = "Tu es un assistant d'exploitation. Résume ce texte en une phrase, puis donne un niveau de gravité.\n\nTexte : {{ inputs.texte }}";

/** Create a skill (first version) or publish a new version of an existing one. Versions are immutable once published. */
export function SkillForm({ skill, folder = "", onClose }: { skill?: AiSkill; folder?: string; onClose: () => void }) {
  const { workspace } = useApp();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const id = useId();
  const latest = skill?.latest_version ?? null;
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [description, setDescription] = useState("");
  const [dir, setDir] = useState(folder);
  const [instructions, setInstructions] = useState(latest?.instructions ?? EXAMPLE);
  const [inputs, setInputs] = useState<Field[]>(latest ? schemaToFields(latest.input_schema) : [{ name: "texte", type: "string", required: true, description: "Le texte à analyser" }]);
  const [outputs, setOutputs] = useState<Field[]>(latest ? schemaToFields(latest.output_schema) : [{ name: "resume", type: "string", required: true, description: "" }, { name: "gravite", type: "string", required: true, description: "" }]);
  const [provider, setProvider] = useState(latest?.default_provider ?? "");
  const [bump, setBump] = useState("minor");
  const [changelog, setChangelog] = useState("");
  const ta = useRef<HTMLTextAreaElement>(null);
  const providers = useWsQuery<{ results: { name: string; cloud: boolean }[] }>("aiproviders-pick", "/ai/providers/");

  const version = { instructions, input_schema: fieldsToSchema(inputs, true), output_schema: fieldsToSchema(outputs, false), default_provider: provider, changelog, ...(skill ? { bump } : {}) };
  const save = useMutation<AiSkill | unknown, ApiError>({
    mutationFn: () => skill
      ? api(`/workspaces/${workspace.slug}/ai/skills/${skill.slug}/versions/`, { json: version })
      : api<AiSkill>(`/workspaces/${workspace.slug}/ai/skills/`, { json: { slug, name, description, folder: dir, version } }),
    onSuccess: (r) => { void qc.invalidateQueries({ queryKey: ["ai-skills"] }); void qc.invalidateQueries({ queryKey: ["ai-skill"] }); onClose(); if (!skill) navigate(`/library/skills/${(r as AiSkill).slug}`); },
  });
  const insert = (n: string) => {
    const el = ta.current; const text = `{{ inputs.${n} }}`;
    if (!el) return setInstructions(`${instructions}${text}`);
    const [a, b] = [el.selectionStart, el.selectionEnd];
    setInstructions(instructions.slice(0, a) + text + instructions.slice(b));
    requestAnimationFrame(() => { el.focus(); el.setSelectionRange(a + text.length, a + text.length); });
  };
  return (
    <Overlay wide title={skill ? `Nouvelle version de « ${skill.name} »` : "Nouveau skill IA"} onClose={onClose}
      description={skill ? "Une version publiée est immuable : les workflows déjà publiés gardent la version qu'ils utilisent." : "Un skill décrit une compétence IA réutilisable : instructions, entrées et format de la réponse. Il n'appelle aucun modèle tant qu'un workflow ne l'exécute pas."}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="submit" form={`${id}-f`} className="primary" disabled={save.isPending}>{skill ? "Publier la version" : "Créer le skill (1.0.0)"}</button></>}>
      <form id={`${id}-f`} onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        {!skill && (
          <>
            <div className="field"><label htmlFor={`${id}-n`}>Nom</label><input id={`${id}-n`} value={name} required maxLength={160} onChange={(e) => { setName(e.target.value); if (!slugTouched) setSlug(slugifyName(e.target.value)); }} /></div>
            <div className="field"><label htmlFor={`${id}-s`}>Identifiant</label><input id={`${id}-s`} className="mono" value={slug} required pattern="[A-Za-z0-9_-]+" maxLength={50} onChange={(e) => { setSlug(e.target.value); setSlugTouched(true); }} /></div>
            <div className="field"><label htmlFor={`${id}-d`}>Description</label><input id={`${id}-d`} value={description} maxLength={2000} onChange={(e) => setDescription(e.target.value)} /></div>
            <div className="field"><label htmlFor={`${id}-f2`}>Dossier (facultatif)</label><input id={`${id}-f2`} value={dir} placeholder="ex. Texte/Analyse" onChange={(e) => setDir(e.target.value)} /></div>
          </>
        )}
        <div className="field">
          <label htmlFor={`${id}-i`}>Instructions données à l'IA</label>
          <textarea id={`${id}-i`} ref={ta} rows={8} className="mono" value={instructions} onChange={(e) => setInstructions(e.target.value)} required />
          <p className="hint">Référencez les entrées avec <code>{"{{ inputs.nom }}"}</code>. Ne mettez jamais de secret ici : la réponse du modèle reste une donnée non fiable, vérifiée contre le format ci-dessous.</p>
          {inputs.some((f) => f.name) && (
            <div><label className="sr-only" htmlFor={`${id}-ins`}>Insérer une entrée dans les instructions</label>
              <select id={`${id}-ins`} value="" onChange={(e) => e.target.value && insert(e.target.value)}><option value="">Insérer une entrée…</option>{inputs.filter((f) => f.name).map((f) => <option key={f.name} value={f.name}>{f.name}</option>)}</select></div>
          )}
        </div>
        <FieldsEditor legend="Entrées du skill" fields={inputs} onChange={setInputs} withRequired minRows={0} idPrefix={`${id}-in`} addLabel="Ajouter une entrée" />
        <FieldsEditor legend="Champs de la réponse attendue" fields={outputs} onChange={setOutputs} withRequired={false} minRows={1} idPrefix={`${id}-out`} addLabel="Ajouter un champ de réponse" />
        <div className="field"><label htmlFor={`${id}-p`}>Fournisseur d'IA par défaut (facultatif)</label>
          <select id={`${id}-p`} value={provider} onChange={(e) => setProvider(e.target.value)}><option value="">Aucun : choisi dans chaque workflow</option>{providers.data?.results.map((p) => <option key={p.name} value={p.name}>{p.name} {p.cloud ? "(cloud)" : "(local)"}</option>)}</select></div>
        {skill && (
          <div className="inline-form">
            <div className="field"><label htmlFor={`${id}-b`}>Type de version</label><select id={`${id}-b`} value={bump} onChange={(e) => setBump(e.target.value)}><option value="patch">Correctif</option><option value="minor">Évolution</option><option value="major">Changement majeur</option></select></div>
            <div className="field"><label htmlFor={`${id}-c`}>Note de version</label><input id={`${id}-c`} value={changelog} maxLength={300} onChange={(e) => setChangelog(e.target.value)} /></div>
          </div>
        )}
        {save.error && <p role="alert" className="field-error">Erreur : {errorText(save.error)}</p>}
      </form>
    </Overlay>
  );
}
