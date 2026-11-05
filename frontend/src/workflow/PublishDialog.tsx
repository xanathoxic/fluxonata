import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import type { ApiError } from "../api";
import { Overlay } from "../components/Overlay";
import { useToast } from "../components/Toast";
import { errorText, useWsMutation, useWsQuery } from "../lib";
import { useDraft } from "./DraftContext";
import type { Issue } from "./types";

const BUMPS = [
  { id: "patch", label: "Correction", hint: "une erreur corrigée, rien ne change pour ceux qui le lancent" },
  { id: "minor", label: "Évolution", hint: "du nouveau, compatible avec les lancements existants" },
  { id: "major", label: "Changement important", hint: "les entrées ou les résultats changent : à annoncer" },
];

/** The publication window, opened from the header: type of change in plain words, a note, and what depends on this workflow. */
export function PublishDialog({ onClose }: { onClose: () => void }) {
  const d = useDraft();
  const toast = useToast();
  const [bump, setBump] = useState("minor");
  const [changelog, setChangelog] = useState("");
  const [problem, setProblem] = useState("");
  const errors = d.issues.filter((i) => i.level === "error").length;
  const usages = useWsQuery<{ results: { workflow: string; workflow_version: string }[] }>(`wf-usages-${d.slug}`, `/workflows/${d.slug}/dependents/`);
  const last = d.wf.latest_version?.version;
  const publish = useWsMutation<{ bump: string; changelog: string }, { version: string }>("POST", `/workflows/${d.slug}/publish/`, [`wf-versions-${d.slug}`, "workflows"]);
  const go = async (e: FormEvent) => {
    e.preventDefault();
    setProblem("");
    if (d.saveState !== "saved") await d.save();
    publish.mutate({ bump, changelog }, {
      onSuccess: (v) => { void d.refetchWorkflow(); toast.show(`Version ${v.version} publiée : elle est lançable.`); onClose(); },
      onError: (err: ApiError) => {
        const list = (err.details as { issues?: Issue[] } | null)?.issues;
        if (list) d.setServerIssues(list);
        setProblem(list ? "Publication impossible : corrigez les problèmes signalés dans l'onglet Conception." : errorText(err));
      },
    });
  };
  const used = usages.data?.results ?? [];
  return (
    <Overlay variant="modal" title="Publier une version" description={last ? `Dernière version publiée : v${last}. Le brouillon actuel deviendra la suivante, immuable.` : "Jamais publié : le brouillon deviendra la version 1.0.0, immuable et lançable."} onClose={onClose}
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="submit" form="wf-pub" className="primary" disabled={errors > 0 || d.graph.nodes.length === 0 || publish.isPending}>Publier</button></>}>
      <form id="wf-pub" onSubmit={(e) => void go(e)}>
        <fieldset className="field"><legend>Type de changement</legend>
          {BUMPS.map((b) => (
            <label key={b.id} className="grp-opt"><input type="radio" name="wf-bump" value={b.id} checked={bump === b.id} onChange={() => setBump(b.id)} /> <span><strong>{b.label}</strong> <span className="hint">— {b.hint}</span></span></label>
          ))}
        </fieldset>
        <div className="field"><label htmlFor="wf-chg">Note de version (facultatif)</label><input id="wf-chg" value={changelog} maxLength={300} onChange={(e) => setChangelog(e.target.value)} /></div>
        {used.length > 0 && <p className="notice" role="note">{used.length} workflow(s) publié(s) appellent celui-ci : ils gardent la version avec laquelle ils ont été publiés.</p>}
        {errors > 0 && <p role="alert" className="field-error">{errors} problème(s) bloquant(s) à corriger avant de publier. <Link to={`/workflows/${d.slug}/schema`} onClick={onClose}>Ouvrir la conception</Link></p>}
        {problem && <p role="alert" className="field-error">{problem}</p>}
      </form>
    </Overlay>
  );
}
