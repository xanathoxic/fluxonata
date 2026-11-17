import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type ApiError } from "../api";
import { useApp } from "../context";
import { errorText } from "../lib";
import { Overlay } from "./Overlay";

interface Result {
  slug: string;
  name?: string;
  status: "published" | "skipped" | "error";
  version?: string;
  message?: string;
}
interface Reply {
  results: Result[];
  published: number;
  skipped: number;
  error: number;
}
const BUMPS = [
  { id: "patch", label: "Correction", hint: "x.y.Z" },
  { id: "minor", label: "Évolution", hint: "x.Y.0" },
  { id: "major", label: "Changement majeur", hint: "X.0.0" },
];
const SAYS = { published: "publié", skipped: "inchangé", error: "refusé" } as const;

/** Publie plusieurs workflows d'un coup. Chacun est validé et publié à part : un refus est expliqué et n'empêche pas les autres ; un brouillon inchangé
 *  n'est pas republié ; un workflow qui en appelle un autre du lot passe après lui. */
export function BulkPublish({ items, onClose, onDone }: { items: { slug: string; name: string }[]; onClose: () => void; onDone: () => void }) {
  const { workspace } = useApp();
  const qc = useQueryClient();
  const [bump, setBump] = useState("minor");
  const [changelog, setChangelog] = useState("");
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState<Reply | null>(null);
  const [problem, setProblem] = useState("");
  const names = new Map(items.map((i) => [i.slug, i.name]));

  const go = async () => {
    setBusy(true);
    setProblem("");
    try {
      const r = await api<Reply>(`/workspaces/${workspace.slug}/workflows/publish-many/`, { json: { slugs: items.map((i) => i.slug), bump, changelog } });
      setReply(r);
      void qc.invalidateQueries({ queryKey: ["workflows"] });
    } catch (e) {
      setProblem(errorText(e as ApiError));
    } finally {
      setBusy(false);
    }
  };

  if (reply) {
    return (
      <Overlay
        variant="modal"
        title="Publication terminée"
        description={`${reply.published} publié(s), ${reply.skipped} inchangé(s), ${reply.error} refusé(s).`}
        onClose={() => {
          onDone();
          onClose();
        }}
        footer={
          <button
            type="button"
            className="primary"
            onClick={() => {
              onDone();
              onClose();
            }}
          >
            Fermer
          </button>
        }
      >
        <ul aria-label="Résultat par workflow" className="usage-list">
          {reply.results.map((r) => (
            <li key={r.slug}>
              <strong>{r.name ?? names.get(r.slug) ?? r.slug}</strong> — {SAYS[r.status]}
              {r.version ? ` (v${r.version})` : ""}
              {r.status !== "published" && r.message ? ` : ${r.message}` : ""}
            </li>
          ))}
        </ul>
      </Overlay>
    );
  }
  return (
    <Overlay
      variant="modal"
      title={`Publier ${items.length} workflow${items.length > 1 ? "s" : ""}`}
      description="Chaque brouillon devient la version suivante de son workflow, immuable et lançable."
      onClose={onClose}
      footer={
        <>
          <button type="button" onClick={onClose}>
            Annuler
          </button>
          <button type="submit" form="wf-bulk" className="primary" disabled={busy}>
            Publier {items.length > 1 ? `les ${items.length}` : "le workflow"}
          </button>
        </>
      }
    >
      <form
        id="wf-bulk"
        onSubmit={(e) => {
          e.preventDefault();
          void go();
        }}
      >
        <ul aria-label="Workflows à publier" className="usage-list">
          {items.map((i) => (
            <li key={i.slug}>{i.name}</li>
          ))}
        </ul>
        <fieldset className="field">
          <legend>Type de changement (le même pour tous)</legend>
          {BUMPS.map((b) => (
            <label key={b.id} className="grp-opt">
              <input type="radio" name="bulk-bump" value={b.id} checked={bump === b.id} onChange={() => setBump(b.id)} />{" "}
              <span>
                <strong>{b.label}</strong> <span className="hint">— {b.hint}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <div className="field">
          <label htmlFor="bulk-log">Note de version (facultative)</label>
          <input id="bulk-log" maxLength={300} value={changelog} onChange={(e) => setChangelog(e.target.value)} />
        </div>
        <p className="hint">Un workflow invalide ou vide est refusé et expliqué sans arrêter les autres ; un brouillon inchangé n'est pas republié.</p>
        {problem && (
          <p role="alert" className="field-error">
            {problem}
          </p>
        )}
      </form>
    </Overlay>
  );
}
