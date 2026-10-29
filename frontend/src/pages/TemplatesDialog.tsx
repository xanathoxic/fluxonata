import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type ApiError } from "../api";
import { Overlay } from "../components/Overlay";
import { useApp } from "../context";
import { errorText, useWsMutation, useWsQuery } from "../lib";
import { HelpTip } from "../components/HelpTip";

interface Tpl { slug: string; name: string; category: string; description: string; requires: string[]; steps: number }

/** Gallery of starter workflows (built-in and the workspace's own) and import of an exported workflow. */
export function TemplatesDialog({ onClose }: { onClose: () => void }) {
  const { workspace } = useApp();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const q = useWsQuery<{ results: Tpl[] }>("workflow-templates", "/workflow-templates/");
  const use = useWsMutation<{ slug: string; name?: string }, { slug: string }>("POST", (i) => `/workflow-templates/${i.slug}/use/`, ["workflows"]);
  const [filter, setFilter] = useState("");
  const [importError, setImportError] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const rows = (q.data?.results ?? []).filter((t) => !filter || `${t.name} ${t.description} ${t.category}`.toLowerCase().includes(filter.toLowerCase()));
  const categories = [...new Set(rows.map((t) => t.category))];
  const open = (slug: string) => { void qc.invalidateQueries({ queryKey: ["workflows"] }); navigate(`/workflows/${slug}/schema`); };
  const doImport = async (f: File) => {
    setImportError("");
    try {
      const doc = JSON.parse(await f.text());
      const r = await api<{ slug: string }>(`/workspaces/${workspace.slug}/workflows/import/`, { json: doc });
      open(r.slug);
    } catch (e) { setImportError(e instanceof SyntaxError ? "Ce fichier n'est pas du JSON valide." : errorText(e as ApiError)); }
  };
  return (
    <Overlay title="Partir d'un modèle" description="Un modèle crée un brouillon prêt à configurer : il n'est jamais publié ni lancé tant que vous ne le décidez pas." onClose={onClose}
      footer={<button type="button" onClick={onClose}>Fermer</button>}>
      <div className="field"><label htmlFor="tpl-q">Rechercher un modèle</label><input id="tpl-q" type="search" value={filter} onChange={(e) => setFilter(e.target.value)} /></div>
      {categories.map((c) => (
        <section key={c} aria-label={c}><h3>{c}</h3>
          <ul className="plain">{rows.filter((t) => t.category === c).map((t) => (
            <li key={t.slug} className="card-lite">
              <strong>{t.name}</strong> <span className="hint">({t.steps} étape{t.steps > 1 ? "s" : ""}{t.requires.length ? ` · connexions : ${t.requires.join(", ")}` : ""})</span>
              <p>{t.description}</p>
              <button type="button" className="primary" disabled={use.isPending} onClick={() => use.mutate({ slug: t.slug }, { onSuccess: (r) => open(r.slug) })} aria-label={`Utiliser le modèle ${t.name}`}>Utiliser</button>
            </li>))}</ul></section>))}
      {q.isSuccess && rows.length === 0 && <p className="hint">Aucun modèle ne correspond.</p>}
      {use.error && <p role="alert" className="field-error">Erreur : {errorText(use.error)}</p>}
      <h3>Importer un workflow exporté <HelpTip>Fichier JSON produit par « Exporter » (onglet Résumé d'un workflow, sur cette plateforme ou une autre).</HelpTip></h3>
      <input ref={file} type="file" accept="application/json,.json" aria-label="Fichier de workflow à importer" onChange={(e) => { const f = e.target.files?.[0]; if (f) void doImport(f); }} />
      {importError && <p role="alert" className="field-error">Erreur : {importError}</p>}
    </Overlay>
  );
}
