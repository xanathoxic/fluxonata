import { type Dispatch, type SetStateAction, useState } from "react";
import { RowIcon } from "../../components/RowIcon";
import { useWsQuery } from "../../lib";

interface RoleRow {
  name: string;
  summary?: string;
  description: string;
  latest_version: string | null;
  versions: { version: string }[];
}

/** Les rôles Ansible qu'une action déclare : { role, version } ; « latest » est figé par le serveur à la publication. */
export function rolesPayload(chosen: Record<string, string>): { role: string; version: string }[] {
  return Object.entries(chosen).map(([role, version]) => ({ role, version: version || "latest" }));
}

/** L'inverse de `rolesPayload` : les rôles déjà déclarés par une version, pour préremplir le formulaire. */
export function rolesChosen(declared: { role: string; version: string }[] | undefined): Record<string, string> {
  return Object.fromEntries((declared ?? []).map((r) => [r.role, r.version]));
}

/** Étiquettes de runner et rôles d'une action Ansible (création d'une action et nouvelle version). */
export function AnsibleFields({
  runnerTags,
  setRunnerTags,
  roles,
  setRoles,
  idPrefix,
}: {
  runnerTags: string;
  setRunnerTags: (v: string) => void;
  roles: Record<string, string>;
  setRoles: Dispatch<SetStateAction<Record<string, string>>>;
  idPrefix: string;
}) {
  const library = useWsQuery<RoleRow[]>("ansible-roles", "/ansible-roles/");
  const [query, setQuery] = useState("");
  const chosen = Object.keys(roles);
  const available = (library.data ?? []).filter((r) => !(r.name in roles));
  const needle = query.trim().toLowerCase();
  const matches = available.filter((r) => `${r.name} ${r.description}`.toLowerCase().includes(needle)).slice(0, 8);
  const add = (name: string) => {
    setRoles((cur) => ({ ...cur, [name]: "" }));
    setQuery("");
  };
  return (
    <fieldset className="field ans-fields" aria-describedby={`${idPrefix}-ans-help`}>
      <legend>Ansible</legend>
      <p id={`${idPrefix}-ans-help`} className="hint">
        Un playbook ne part que sur un runner qui porte l'une de ces étiquettes : choisissez celles du runner (ou du worker) qui voit vos machines. Les rôles
        cochés sont figés à la publication et disponibles par leur nom dans le playbook.
      </p>
      <div className="ans-col">
        <label htmlFor={`${idPrefix}-rt`}>Étiquettes de runner exigées (séparées par des virgules)</label>
        <input id={`${idPrefix}-rt`} value={runnerTags} onChange={(e) => setRunnerTags(e.target.value)} required />
      </div>
      <div className="ans-col">
        <p className="hint" id={`${idPrefix}-roles-h`}>
          Rôles de l'action
        </p>
        {chosen.length === 0 && <p className="hint">Aucun rôle déclaré.</p>}
        {chosen.length > 0 && (
          <ul className="role-list" aria-labelledby={`${idPrefix}-roles-h`}>
            {chosen.map((name) => {
              const r = (library.data ?? []).find((x) => x.name === name);
              return (
                <li key={name} className="role-row">
                  <span className="role-id">
                    <strong>{name}</strong>
                    {(r?.summary || r?.description) && <small className="hint">{r.summary || r.description}</small>}
                  </span>
                  <select
                    aria-label={`Version du rôle ${name}`}
                    title="La version choisie est figée à la publication ; « Dernière » prend la plus récente à ce moment-là."
                    value={roles[name] ?? ""}
                    onChange={(e) => setRoles((cur) => ({ ...cur, [name]: e.target.value }))}
                  >
                    <option value="">Dernière{r?.latest_version ? ` (${r.latest_version})` : ""}</option>
                    {(r?.versions ?? []).map((v) => (
                      <option key={v.version} value={v.version}>
                        {v.version}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="icon-btn"
                    title="Retirer ce rôle"
                    aria-label={`Retirer le rôle ${name}`}
                    onClick={() =>
                      setRoles((cur) => {
                        const { [name]: _drop, ...rest } = cur;
                        void _drop;
                        return rest;
                      })
                    }
                  >
                    <RowIcon name="trash" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        <label htmlFor={`${idPrefix}-search`}>Ajouter un rôle (rechercher dans la bibliothèque)</label>
        <input
          id={`${idPrefix}-search`}
          type="search"
          value={query}
          autoComplete="off"
          placeholder={`${available.length} rôle(s) disponible(s)`}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              const first = matches[0];
              if (first) add(first.name);
            }
          }}
        />
        {query.trim() !== "" && (
          <ul className="role-matches" aria-label="Rôles trouvés">
            {matches.map((r) => (
              <li key={r.name}>
                <button type="button" onClick={() => add(r.name)}>
                  <span className="sr-only">Ajouter le rôle</span> <strong>{r.name}</strong>
                  {r.description ? ` — ${r.description}` : ""} <span className="hint">dernière : {r.latest_version ?? "—"}</span>
                </button>
              </li>
            ))}
            {matches.length === 0 && (
              <li className="hint">
                Aucun rôle ne correspond{(library.data ?? []).length === 0 ? " (la bibliothèque est vide : onglet Rôles Ansible de la page Actions)" : ""}.
              </li>
            )}
          </ul>
        )}
      </div>
    </fieldset>
  );
}
