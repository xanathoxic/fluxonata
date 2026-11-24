export interface RunFileRow {
  id: string;
  step_id: string;
  name: string;
  size: number;
  content_type: string;
  sha256: string;
}

export function sizeLabel(n: number): string {
  return n < 1024 ? `${n} o` : n < 1048576 ? `${(n / 1024).toFixed(1)} Ko` : `${(n / 1048576).toFixed(1)} Mo`;
}

/** Les fichiers produits, bien en vue dans le résumé : le moyen le plus court de récupérer le résultat d'un workflow (rapport PDF, tableur, archive…). */
export function RunDeliverables({ rows, slug, runId }: { rows: RunFileRow[]; slug: string; runId: string }) {
  if (rows.length === 0) return null;
  return (
    <section className="deliverables" aria-labelledby="deliverables-h">
      <h3 id="deliverables-h">{rows.length === 1 ? "Fichier à télécharger" : `${rows.length} fichiers à télécharger`}</h3>
      <ul>
        {rows.map((f) => (
          <li key={f.id}>
            <a href={`/api/v1/workspaces/${slug}/run-files/${f.id}/download/`} download={f.name}>
              {f.name}
            </a>{" "}
            <span className="hint">({sizeLabel(f.size)})</span>
          </li>
        ))}
      </ul>
      {rows.length > 1 && (
        <p>
          <a className="button" href={`/api/v1/workspaces/${slug}/workflow-runs/${runId}/files/archive/`} download>
            Tout télécharger (.zip)
          </a>
        </p>
      )}
    </section>
  );
}
