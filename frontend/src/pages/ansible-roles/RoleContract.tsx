import { useState } from "react";
import { LazyCodeEditor as CodeEditor } from "../../components/LazyCodeEditor";
import { RowIcon } from "../../components/RowIcon";
import { api, type ApiError } from "../../api";
import { Badge } from "../../components/AdminUi";
import { CopyBlock } from "../../components/CopyBlock";
import { defaultValue, shown } from "../../components/SchemaFields";
import { useApp } from "../../context";
import { errorText } from "../../lib";
import { exampleFor, normContract, type RoleContractDoc, type RoleOutput, type RoleVariable, VARIABLE_TYPES, viewContract } from "./roles";

const JSON_TYPE: Record<string, string> = { int: "integer", float: "number", bool: "boolean", list: "array", dict: "object" };
const IDENT = /[^A-Za-z0-9_]/g;
const typeLabel = (t: string) => VARIABLE_TYPES.find(([k]) => k === t)?.[1] ?? t;

/** Lecture du contrat : à quoi sert le rôle, ce qu'il attend, ce qu'il produit, un exemple d'appel. */
export function ContractDoc({ role, contract }: { role: string; contract: RoleContractDoc | undefined }) {
  const c = normContract(contract);
  const empty = !c.summary && !c.description && c.variables.length === 0 && c.outputs.length === 0 && !c.example;
  if (empty)
    return (
      <p className="hint">
        Ce rôle n'a pas de contrat : on ne sait ni à quoi il sert ni quelles variables il attend. Lisez ses fichiers, ou ajoutez le contrat dans une nouvelle
        version.
      </p>
    );
  return (
    <div className="role-doc">
      {c.summary && <p className="role-doc-summary">{c.summary}</p>}
      {c.description && <p>{c.description}</p>}
      <h3>Variables</h3>
      {c.variables.length === 0 ? (
        <p className="hint">Aucune variable : le rôle s'appelle sans paramètre.</p>
      ) : (
        <ul className="role-vars" aria-label={`Variables du rôle ${role}`}>
          {c.variables.map((v) => (
            <li key={v.name}>
              <div className="role-var-head">
                <code className="role-var-name">{v.name}</code>
                <span className="role-var-type">{typeLabel(v.type)}</span>
                {v.required ? <Badge tone="warn">Obligatoire</Badge> : <span className="hint">facultative</span>}
                {v.default !== undefined && (
                  <span className="hint">
                    défaut <code>{shown(v.default)}</code>
                  </span>
                )}
              </div>
              <p className="role-var-desc">{v.description || <span className="hint">Pas de description.</span>}</p>
            </li>
          ))}
        </ul>
      )}
      {c.outputs.length > 0 && (
        <>
          <h3>Ce que le rôle produit</h3>
          <ul>
            {c.outputs.map((o) => (
              <li key={o.name}>
                <code>{o.name}</code> : {o.description || "—"}
              </li>
            ))}
          </ul>
        </>
      )}
      {c.example && (
        <>
          <h3>Exemple d'appel</h3>
          <CopyBlock text={c.example} label={`Exemple d'appel du rôle ${role}`} />
        </>
      )}
    </div>
  );
}

interface Advice {
  text: string;
  target: string;
}

/** Ce qu'il reste à écrire, avec le champ à rejoindre : un clic y amène le focus. */
function adviceFor(c: ReturnType<typeof viewContract>): Advice[] {
  return [
    ...(c.summary.trim() ? [] : [{ text: "Dites en une phrase à quoi sert le rôle.", target: "rc-summary" }]),
    ...c.variables.flatMap((v, i) => (v.description.trim() ? [] : [{ text: `Décrivez la variable « ${v.name} ».`, target: `rc-ve-${i}` }])),
  ];
}

const focusField = (id: string) => {
  const el = document.getElementById(id);
  el?.scrollIntoView({ block: "center" });
  el?.focus();
};

/** Le contrat d'usage d'un rôle (version choisie) : en lecture pour tous, modifiable dans une nouvelle version par qui peut publier. À droite, l'aperçu de ce que verront les utilisateurs. */
export function RoleContract({
  role,
  contract,
  files,
  canEdit,
  onChange,
}: {
  role: string;
  contract: RoleContractDoc | undefined;
  files: Record<string, string>;
  canEdit: boolean;
  onChange: (c: RoleContractDoc) => void;
}) {
  const { workspace } = useApp();
  const c = viewContract(contract);
  const [drafting, setDrafting] = useState<string | null>(null);
  const [draftError, setDraftError] = useState("");
  const [copied, setCopied] = useState(false);
  if (!canEdit)
    return (
      <section aria-label={`Contrat du rôle ${role}`}>
        <ContractDoc role={role} contract={contract} />
      </section>
    );

  const advice = adviceFor(c);
  const documented = c.variables.filter((v) => v.description.trim()).length;
  const set = (patch: Partial<RoleContractDoc>) => onChange({ ...c, ...patch });
  const setVar = (i: number, p: Partial<RoleVariable>) => set({ variables: c.variables.map((v, j) => (j === i ? { ...v, ...p } : v)) });
  const setOut = (i: number, p: Partial<RoleOutput>) => set({ outputs: c.outputs.map((o, j) => (j === i ? { ...o, ...p } : o)) });
  const taken = (list: { name: string }[], i: number, name: string) => !name || list.some((x, j) => j !== i && x.name === name);
  const move = (i: number, by: number) => {
    const next = [...c.variables];
    const [item] = next.splice(i, 1);
    if (item) next.splice(i + by, 0, item);
    set({ variables: next });
  };
  const draft = async () => {
    setDraftError("");
    setDrafting("…");
    try {
      const r = await api<{ variables: RoleVariable[] }>(`/workspaces/${workspace.slug}/ansible-roles/contract-draft/`, { json: { files } });
      const known = new Set(c.variables.map((v) => v.name));
      const added = r.variables.filter((v) => !known.has(v.name));
      set({ variables: [...c.variables, ...added] });
      setDrafting(added.length > 0 ? `${added.length} variable(s) ajoutée(s) depuis defaults/main.yml.` : "Aucune variable nouvelle dans defaults/main.yml.");
    } catch (e) {
      setDrafting(null);
      setDraftError(errorText(e as ApiError));
    }
  };
  const copyExample = async () => {
    try {
      await navigator.clipboard.writeText(c.example);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };
  const addVariable = () => {
    let n = c.variables.length + 1;
    while (c.variables.some((v) => v.name === `variable_${n}`)) n++;
    set({ variables: [...c.variables, { name: `variable_${n}`, type: "str", required: false, description: "" }] });
    setTimeout(() => focusField(`rc-vn-${c.variables.length}`), 0);
  };
  const addOutput = () => {
    let n = c.outputs.length + 1;
    while (c.outputs.some((o) => o.name === `resultat_${n}`)) n++;
    set({ outputs: [...c.outputs, { name: `resultat_${n}`, description: "" }] });
  };
  return (
    <section aria-label={`Contrat du rôle ${role}`} className="rc">
      <header className={`rc-status${advice.length === 0 ? " ok" : ""}`}>
        <div className="rc-status-main">
          <strong>{advice.length === 0 ? "✔ Contrat complet" : `${advice.length} point(s) à compléter`}</strong>
          <span className="hint">
            {c.variables.length > 0 ? `${documented}/${c.variables.length} variable(s) décrite(s)` : "Aucune variable déclarée"} · enregistré avec la prochaine
            version publiée
          </span>
        </div>
        {advice.length > 0 && (
          <ul className="rc-advice" aria-label="À compléter">
            {advice.slice(0, 4).map((a) => (
              <li key={a.text}>
                <button type="button" className="linklike" onClick={() => focusField(a.target)}>
                  {a.text}
                </button>
              </li>
            ))}
            {advice.length > 4 && <li className="hint">… et {advice.length - 4} autre(s)</li>}
          </ul>
        )}
      </header>
      <div className="rc-layout">
        <div className="rc-edit">
          <section className="np-section" aria-labelledby="rc-pres">
            <h3 id="rc-pres">Présentation</h3>
            <div className="field">
              <label htmlFor="rc-summary">À quoi sert le rôle (une phrase)</label>
              <input
                id="rc-summary"
                value={c.summary}
                maxLength={200}
                onChange={(e) => set({ summary: e.target.value })}
                placeholder="ex. Installe et configure le serveur web"
                aria-invalid={!c.summary.trim()}
              />
              <span className="hint rc-count">{c.summary.length}/200</span>
            </div>
            <div className="field">
              <label htmlFor="rc-desc">Description détaillée (facultatif)</label>
              <textarea
                id="rc-desc"
                rows={3}
                value={c.description}
                maxLength={4000}
                onChange={(e) => set({ description: e.target.value })}
                placeholder="Ce que fait le rôle, ses prérequis, ses limites…"
              />
            </div>
          </section>
          <section className="np-section" aria-labelledby="rc-vars">
            <h3 id="rc-vars">
              Variables <span className="hint">({c.variables.length})</span>
            </h3>
            {c.variables.length === 0 ? (
              <p className="hint">Aucune variable : le rôle s'appelle sans paramètre. « Pré-remplir » reprend celles de defaults/main.yml.</p>
            ) : (
              <div className="table-wrap">
                <table className="adm-table rc-table">
                  <caption className="sr-only">Variables du rôle {role}</caption>
                  <colgroup>
                    <col className="rc-c-name" />
                    <col className="rc-c-type" />
                    <col className="rc-c-req" />
                    <col className="rc-c-def" />
                    <col />
                    <col className="rc-c-act" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th scope="col">Nom</th>
                      <th scope="col">Type</th>
                      <th scope="col">Oblig.</th>
                      <th scope="col">Défaut</th>
                      <th scope="col">Description</th>
                      <th scope="col">
                        <span className="sr-only">Actions</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {c.variables.map((v, i) => (
                      // biome-ignore lint/suspicious/noArrayIndexKey: le nom est modifiable : il ne peut pas servir de clé
                      <tr key={i}>
                        <td>
                          <label className="sr-only" htmlFor={`rc-vn-${i}`}>
                            Nom de la variable {i + 1}
                          </label>
                          <input
                            id={`rc-vn-${i}`}
                            className="mono"
                            value={v.name}
                            onChange={(e) => {
                              const n = e.target.value.replace(IDENT, "_");
                              if (!taken(c.variables, i, n)) setVar(i, { name: n });
                            }}
                          />
                        </td>
                        <td>
                          <label className="sr-only" htmlFor={`rc-vt-${i}`}>
                            Type de {v.name}
                          </label>
                          <select id={`rc-vt-${i}`} value={v.type} onChange={(e) => setVar(i, { type: e.target.value, default: undefined })}>
                            {VARIABLE_TYPES.map(([k, l]) => (
                              <option key={k} value={k}>
                                {l}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td className="rc-center">
                          <input type="checkbox" id={`rc-vr-${i}`} checked={v.required} onChange={(e) => setVar(i, { required: e.target.checked })} />
                          <label htmlFor={`rc-vr-${i}`} className="sr-only">
                            {v.name} : obligatoire
                          </label>
                        </td>
                        <td>
                          <label className="sr-only" htmlFor={`rc-vd-${i}`}>
                            Valeur par défaut de {v.name}
                          </label>
                          <input
                            id={`rc-vd-${i}`}
                            className="mono"
                            // biome-ignore lint/suspicious/noArrayIndexKey: la clé ne sert qu'à relire la valeur par défaut quand le type change
                            key={`rc-vd-${i}-${v.type}`}
                            defaultValue={shown(v.default)}
                            placeholder={v.required ? "(aucune)" : ""}
                            onBlur={(e) => setVar(i, { default: defaultValue(e.target.value, JSON_TYPE[v.type] ?? "string") })}
                          />
                        </td>
                        <td>
                          <label className="sr-only" htmlFor={`rc-ve-${i}`}>
                            Description de {v.name}
                          </label>
                          <input
                            id={`rc-ve-${i}`}
                            value={v.description}
                            maxLength={500}
                            onChange={(e) => setVar(i, { description: e.target.value })}
                            placeholder="À quoi sert cette variable, valeurs attendues"
                            aria-invalid={!v.description.trim()}
                          />
                        </td>
                        <td className="rc-actions">
                          <button
                            type="button"
                            className="icon-btn"
                            onClick={() => move(i, -1)}
                            disabled={i === 0}
                            aria-label={`Monter la variable ${v.name}`}
                            title="Monter"
                          >
                            ↑
                          </button>
                          <button
                            type="button"
                            className="icon-btn"
                            onClick={() => move(i, 1)}
                            disabled={i === c.variables.length - 1}
                            aria-label={`Descendre la variable ${v.name}`}
                            title="Descendre"
                          >
                            ↓
                          </button>
                          <button
                            type="button"
                            className="icon-btn danger-icon"
                            onClick={() => set({ variables: c.variables.filter((_, j) => j !== i) })}
                            aria-label={`Supprimer la variable ${v.name}`}
                            title="Supprimer"
                          >
                            <RowIcon name="trash" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="rc-bar">
              <button type="button" onClick={addVariable}>
                + Ajouter une variable
              </button>
              <button type="button" onClick={() => void draft()} disabled={drafting === "…"}>
                Pré-remplir depuis defaults/main.yml
              </button>
              {drafting && drafting !== "…" && (
                <span role="status" className="hint">
                  {drafting}
                </span>
              )}
              {draftError && (
                <span role="alert" className="field-error">
                  {draftError}
                </span>
              )}
            </div>
          </section>
          <section className="np-section" aria-labelledby="rc-out">
            <h3 id="rc-out">
              Ce que le rôle produit <span className="hint">({c.outputs.length})</span>
            </h3>
            <p className="hint">
              Faits publiés (<code>set_fact</code>), statistiques (<code>set_stats</code>) ou résultats enregistrés que le playbook peut lire ensuite.
            </p>
            {c.outputs.map((o, i) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: le nom est modifiable : il ne peut pas servir de clé
              <div className="rc-out-row" key={i}>
                <label className="sr-only" htmlFor={`rc-on-${i}`}>
                  Nom du résultat {i + 1}
                </label>
                <input
                  id={`rc-on-${i}`}
                  className="mono"
                  value={o.name}
                  onChange={(e) => {
                    const n = e.target.value.replace(IDENT, "_");
                    if (!taken(c.outputs, i, n)) setOut(i, { name: n });
                  }}
                />
                <label className="sr-only" htmlFor={`rc-od-${i}`}>
                  Description du résultat {o.name}
                </label>
                <input
                  id={`rc-od-${i}`}
                  value={o.description}
                  maxLength={500}
                  onChange={(e) => setOut(i, { description: e.target.value })}
                  placeholder="À quoi il sert"
                />
                <button
                  type="button"
                  className="icon-btn danger-icon"
                  onClick={() => set({ outputs: c.outputs.filter((_, j) => j !== i) })}
                  aria-label={`Supprimer le résultat ${o.name}`}
                  title="Supprimer"
                >
                  <RowIcon name="trash" />
                </button>
              </div>
            ))}
            <div className="rc-bar">
              <button type="button" onClick={addOutput}>
                + Ajouter un résultat
              </button>
            </div>
          </section>
          <section className="np-section" aria-labelledby="rc-ex">
            <h3 id="rc-ex">Exemple d'appel</h3>
            <div className="rc-example">
              <CodeEditor
                value={c.example}
                onChange={(v) => set({ example: v })}
                language="yaml"
                ariaLabel="Un playbook qui utilise le rôle"
                minHeight="150px"
              />
            </div>
            <div className="rc-bar">
              <button type="button" onClick={() => set({ example: exampleFor(role, c) })}>
                Générer depuis les variables
              </button>
              <button type="button" onClick={() => void copyExample()} disabled={!c.example}>
                Copier
              </button>
              {copied && (
                <span role="status" className="hint">
                  Copié.
                </span>
              )}
            </div>
          </section>
        </div>
        <aside className="rc-preview" aria-label="Aperçu du contrat">
          <p className="rc-preview-title">Aperçu · ce que verront les utilisateurs</p>
          <ContractDoc role={role} contract={c} />
        </aside>
      </div>
    </section>
  );
}
