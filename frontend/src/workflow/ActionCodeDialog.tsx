import { useState } from "react";
import type { Action } from "../api";
import { Badge } from "../components/AdminUi";
import { Overlay } from "../components/Overlay";
import { SourceViewer } from "../components/SourceViewer";
import { useWsQuery } from "../lib";
import type { GraphNode } from "./types";

interface Ver {
  version: string;
  runtime: string;
  source: string;
  source_digest: string;
  side_effects: string;
  idempotent: boolean;
  network_policy: string;
  changelog?: string;
}

/** Fenêtre centrale ouverte par un double clic sur une étape « action » : le code de l'action, en lecture seule. La version affichée est celle que l'étape appelle
 *  (figée à la publication, sinon la plus récente) ; on peut en consulter une autre sans rien modifier. */
export function ActionCodeDialog({ node, action, onClose }: { node: GraphNode; action: Action | undefined; onClose: () => void }) {
  const slug = node.action ?? "";
  const q = useWsQuery<{ results: Ver[] }>(`versions-${slug}`, `/actions/${slug}/versions/`, { enabled: slug !== "" });
  const [picked, setPicked] = useState("");
  const list = q.data?.results ?? [];
  const current = list.find((v) => v.version === picked) ?? list.find((v) => v.version === node.version) ?? list[0];
  const title = action?.name ?? node.name ?? slug;
  return (
    <Overlay
      wide
      variant="modal"
      className="action-code"
      title={title}
      description={action?.description || undefined}
      onClose={onClose}
      footer={
        <button type="button" onClick={onClose}>
          Fermer
        </button>
      }
    >
      {q.isLoading && <p role="status">Chargement du code…</p>}
      {q.isError && <p role="alert">Le code de cette action n'a pas pu être chargé.</p>}
      {!q.isLoading && !q.isError && !current && <p>Cette action n'a aucune version publiée.</p>}
      {current && (
        <>
          <div className="action-code-meta">
            <span className="mono">{slug}</span>
            <Badge tone={current.side_effects === "none" ? "ok" : "warn"}>
              {current.side_effects === "none" ? "Sans effet externe" : current.idempotent ? "Effet externe" : "Effet externe non idempotent"}
            </Badge>
            <Badge tone="info">Réseau : {current.network_policy}</Badge>
            {action?.status === "deprecated" && <Badge tone="warn">Dépréciée</Badge>}
            <label className="action-code-ver">
              Version
              <select value={current.version} onChange={(e) => setPicked(e.target.value)}>
                {list.map((v) => (
                  <option key={v.version} value={v.version}>
                    {v.version}
                    {v === list[0] ? " (dernière)" : ""}
                    {v.version === node.version ? " · appelée par l'étape" : ""}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {current.changelog && <p className="hint">{current.changelog}</p>}
          <SourceViewer source={current.source} runtime={current.runtime} version={current.version} digest={current.source_digest} name={slug} />
        </>
      )}
    </Overlay>
  );
}
