import { useId, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { errorText, useWsMutation, useWsQuery } from "../lib";
import { Overlay } from "./Overlay";
import { HelpTip } from "./HelpTip";

interface Access { results: { folder: string; grants: { username: string; level: string }[] }[]; levels: Record<string, string> }
interface Members { results: { username: string; role: string; is_service: boolean }[] }
const LEVELS: Record<string, string> = { view: "Voir", run: "Voir, lancer et approuver", edit: "Voir, lancer, modifier et publier" };

/** Who may enter a PRIVATE workflow folder (administrators only). A level never goes beyond the person's role in the
 *  space: it can only narrow it. Administrators and space managers always keep access. */
export function FolderShareDialog({ folder, onClose }: { folder: string; onClose: () => void }) {
  const id = useId();
  const qc = useQueryClient();
  const access = useWsQuery<Access>("folder-access", "/folder-access/");
  const members = useWsQuery<Members>("members", "/members/");
  const existing = access.data?.results.find((a) => a.folder === folder);
  const [priv, setPriv] = useState<boolean | null>(null);
  const [grants, setGrants] = useState<Record<string, string> | null>(null);
  const isPrivate = priv ?? !!existing;
  const current = grants ?? Object.fromEntries((existing?.grants ?? []).map((g) => [g.username, g.level]));
  const done = () => { for (const k of ["folder-access", "folders", "workflows"]) void qc.invalidateQueries({ queryKey: [k] }); onClose(); };
  const save = useWsMutation<{ folder: string; grants: { username: string; level: string }[] }>("PUT", "/folder-access/", []);
  const clear = useWsMutation<{ folder: string }>("DELETE", (i) => `/folder-access/?folder=${encodeURIComponent(i.folder)}`, []);
  const people = (members.data?.results ?? []).filter((m) => !["admin", "space_manager"].includes(m.role));
  const submit = () => {
    if (isPrivate) save.mutate({ folder, grants: Object.entries(current).filter(([, l]) => l).map(([username, level]) => ({ username, level })) }, { onSuccess: done });
    else if (existing) clear.mutate({ folder }, { onSuccess: done });
    else onClose();
  };
  const err = save.error ?? clear.error;
  return (
    <Overlay variant="modal" onClose={onClose} title={`Partage du dossier « ${folder} »`} description="Un dossier privé n'est vu, dans la liste, la recherche, les exécutions et l'assistant, que par les personnes ci-dessous (et les administrateurs de l'espace)."
      footer={<><button type="button" onClick={onClose}>Annuler</button><button type="submit" form={`${id}-f`} className="primary" disabled={save.isPending || clear.isPending}>Enregistrer</button></>}>
      <form id={`${id}-f`} onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <div className="field field-inline"><input id={`${id}-p`} type="checkbox" checked={isPrivate} onChange={(e) => setPriv(e.target.checked)} /><label htmlFor={`${id}-p`}>Dossier privé (sous-dossiers compris, sauf s'ils ont leur propre partage)</label></div>
        {isPrivate && (
          <fieldset>
            <legend>Personnes ayant accès</legend>
            {people.length === 0 && <p className="hint">Aucun membre à ajouter : seuls les administrateurs accèdent à ce dossier.</p>}
            {people.map((m) => (
              <div className="field" key={m.username}>
                <label htmlFor={`${id}-${m.username}`}>{m.username}</label>
                <select id={`${id}-${m.username}`} value={current[m.username] ?? ""} onChange={(e) => setGrants({ ...current, [m.username]: e.target.value })}>
                  <option value="">Aucun accès</option>
                  {Object.entries(LEVELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </div>
            ))}
            <p className="help-row"><HelpTip caption="À savoir">Le niveau ne dépasse jamais le rôle de la personne dans l'espace : un lecteur reste lecteur même avec « modifier ».</HelpTip></p>
          </fieldset>
        )}
        {err && <p role="alert" className="field-error">Erreur : {errorText(err)}</p>}
      </form>
    </Overlay>
  );
}
