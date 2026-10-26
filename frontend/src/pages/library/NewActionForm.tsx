import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { errorText, useWsMutation } from "../../lib";

const EXAMPLE_IN = '{"type":"object","required":["n"],"properties":{"n":{"type":"integer","title":"Nombre"}}}';
const EXAMPLE_OUT = '{"type":"object","required":["double"],"properties":{"double":{"type":"integer","title":"Double"}}}';
const EXAMPLE_SRC = "def run(inputs):\n    return {\"double\": inputs[\"n\"] * 2}\n";

/** Advanced form (developers/admins): code + JSON Schemas. Validation is done by the server. */
export function NewActionForm({ onDone, folder = "" }: { onDone: () => void; folder?: string }) {
  const navigate = useNavigate();
  const create = useWsMutation<Record<string, unknown>, { slug: string }>("POST", "/library/actions/", ["actions-lib", "actions"]);
  const [f, setF] = useState({ slug: "", name: "", description: "", folder, runtime: "python", source: EXAMPLE_SRC, input: EXAMPLE_IN, output: EXAMPLE_OUT, side: "none", network: "none" });
  const [jsonError, setJsonError] = useState("");
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    let input_schema, output_schema;
    try { input_schema = JSON.parse(f.input); output_schema = JSON.parse(f.output); setJsonError(""); } catch { setJsonError("Un des schémas n'est pas du JSON valide."); return; }
    create.mutate({ slug: f.slug, name: f.name, description: f.description, folder: f.folder, version: { runtime: f.runtime, source: f.source, input_schema, output_schema, side_effects: f.side, network_policy: f.network } },
      { onSuccess: (a) => { onDone(); navigate(`/library/actions/${a.slug}`); } });
  };
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  return (
    <form onSubmit={submit} aria-label="Nouvelle action">
      <div className="field"><label htmlFor="na-s">Identifiant (lettres, chiffres, tirets)</label><input id="na-s" value={f.slug} onChange={set("slug")} pattern="[A-Za-z0-9_-]+" required /></div>
      <div className="field"><label htmlFor="na-n">Nom</label><input id="na-n" value={f.name} onChange={set("name")} required /></div>
      <div className="field"><label htmlFor="na-d">Description</label><input id="na-d" value={f.description} onChange={set("description")} /></div>
      <div className="field"><label htmlFor="na-f">Dossier (facultatif)</label><input id="na-f" value={f.folder} onChange={set("folder")} placeholder="ex. Réseau/DNS" /></div>
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
      {create.error && <p role="alert" className="field-error">Erreur : {errorText(create.error)}</p>}
      <button type="submit" className="primary" disabled={create.isPending}>Publier la version 1.0.0</button>
    </form>
  );
}
