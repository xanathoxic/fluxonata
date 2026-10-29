import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { ApiError } from "../../api";
import { errorText, useWsMutation, useWsQuery } from "../../lib";
import { TagInput } from "../../components/Tags";
import type { ActionProposal } from "./ActionAssistant";

const EXAMPLE_IN = '{"type":"object","required":["n"],"properties":{"n":{"type":"integer","title":"Nombre","description":"Le nombre à doubler."}}}';
const EXAMPLE_OUT = '{"type":"object","required":["double"],"properties":{"double":{"type":"integer","title":"Double","description":"Le nombre reçu multiplié par deux."}}}';
const EXAMPLE_SRC = "def run(inputs):\n    return {\"double\": inputs[\"n\"] * 2}\n";

/** Advanced form (developers/admins): code + JSON Schemas. Validation is done by the server. */
export function NewActionForm({ onDone, folder = "", initial }: { onDone: () => void; folder?: string; initial?: ActionProposal }) {
  const navigate = useNavigate();
  const create = useWsMutation<Record<string, unknown>, { slug: string }>("POST", "/library/actions/", ["actions-lib", "actions"]);
  const [f, setF] = useState(initial
    ? { slug: initial.slug, name: initial.name, description: initial.description, folder, runtime: initial.runtime, source: initial.source, input: JSON.stringify(initial.input_schema, null, 2), output: JSON.stringify(initial.output_schema, null, 2), side: initial.side_effects, network: initial.network_policy }
    : { slug: "", name: "", description: "", folder, runtime: "python", source: EXAMPLE_SRC, input: EXAMPLE_IN, output: EXAMPLE_OUT, side: "none", network: "none" });
  const [reviewed, setReviewed] = useState(false);
  const [jsonError, setJsonError] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const known = useWsQuery<{ results: { tags?: string[] }[] }>("actions-tags", "/actions/");
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    let input_schema, output_schema;
    try { input_schema = JSON.parse(f.input); output_schema = JSON.parse(f.output); setJsonError(""); } catch { setJsonError("Un des schémas n'est pas du JSON valide."); return; }
    create.mutate({ slug: f.slug, name: f.name, description: f.description, folder: f.folder, tags, version: { runtime: f.runtime, source: f.source, input_schema, output_schema, side_effects: f.side, network_policy: f.network, ...(initial?.examples?.length ? { examples: initial.examples } : {}) }, ...(initial ? { ai_generated: true, reviewed } : {}) },
      { onSuccess: (a) => { onDone(); navigate(`/library/actions/${a.slug}`); } });
  };
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <form onSubmit={submit} aria-label="Nouvelle action">
      {initial && <p role="note" className="notice">✨ Proposée par l'assistant IA : relisez le code et le contrat, corrigez ce qui doit l'être, puis confirmez la relecture pour publier.</p>}
      <div className="field"><label htmlFor="na-s">Identifiant (lettres, chiffres, tirets)</label><input id="na-s" value={f.slug} onChange={set("slug")} pattern="[A-Za-z0-9_-]+" required /></div>
      <div className="field"><label htmlFor="na-n">Nom</label><input id="na-n" value={f.name} onChange={set("name")} required /></div>
      <div className="field"><label htmlFor="na-d">Ce que fait l'action (une ou deux phrases)</label><input id="na-d" value={f.description} onChange={set("description")} aria-describedby="na-contract" /></div>
      <p id="na-contract" className="hint">Le contrat est obligatoire : décrivez ce que fait l'action ; pour chaque entrée et chaque sortie, un type, un titre et une description (à quoi ça sert) ; et, s'il n'y en a aucune, dites-le dans la « description » du schéma (ex. « Cette action n'a aucune sortie. »).</p>
      <div className="field"><label htmlFor="na-f">Dossier (facultatif)</label><input id="na-f" value={f.folder} onChange={set("folder")} placeholder="ex. Réseau/DNS" /></div>
      <TagInput value={tags} onChange={setTags} suggestions={[...new Set((known.data?.results ?? []).flatMap((a) => a.tags ?? []))].sort()} />
      <div className="field"><label htmlFor="na-r">Langage</label>
        <select id="na-r" value={f.runtime} onChange={set("runtime")}><option value="python">Python</option><option value="node">Node.js</option><option value="typescript">TypeScript</option><option value="shell">Shell</option><option value="powershell">PowerShell</option></select></div>
      <div className="field"><label htmlFor="na-c">Code (point d'entrée : run)</label><textarea id="na-c" rows={8} className="mono" value={f.source} onChange={set("source")} spellCheck={false} /></div>
      <div className="field"><label htmlFor="na-i">Schéma des entrées (JSON Schema)</label><textarea id="na-i" rows={4} className="mono" value={f.input} onChange={set("input")} /></div>
      <div className="field"><label htmlFor="na-o">Schéma des sorties (JSON Schema)</label><textarea id="na-o" rows={4} className="mono" value={f.output} onChange={set("output")} /></div>
      <div className="field"><label htmlFor="na-e">Effets de bord</label>
        <select id="na-e" value={f.side} onChange={set("side")}><option value="none">Aucun effet externe</option><option value="external">Effet externe (non rejoué automatiquement si l'issue est incertaine)</option></select></div>
      <div className="field"><label htmlFor="na-net">Politique réseau</label>
        <select id="na-net" value={f.network} onChange={set("network")}><option value="none">Aucun accès réseau</option><option value="test-service">Service de test local uniquement</option></select></div>
      {jsonError && <p role="alert" className="field-error">Erreur : {jsonError}</p>}
      {contractProblems(create.error) ? (
        <div role="alert" className="field-error">Le contrat n'est pas complet :<ul>{contractProblems(create.error)?.map((p) => <li key={p}>{p}</li>)}</ul></div>
      ) : create.error && <p role="alert" className="field-error">Erreur : {errorText(create.error)}</p>}
      {initial && <div className="field field-inline"><input id="na-rev" type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} /><label htmlFor="na-rev">J'ai relu ce code et ce contrat</label></div>}
      <button type="submit" className="primary" disabled={create.isPending || (!!initial && !reviewed)}>Publier la version 1.0.0</button>
    </form>
  );
}

function contractProblems(e: unknown): string[] | null {
  const d = (e as Partial<ApiError> | null)?.details as { contract?: unknown } | null | undefined;
  return d && Array.isArray(d.contract) && d.contract.length ? (d.contract as string[]) : null;
}
