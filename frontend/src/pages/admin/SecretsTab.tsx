import { useMemo, useState } from "react";
import { Badge, Confirm, EmptyRow, SearchBox, Section, usePaged } from "../../components/AdminUi";
import { Overlay } from "../../components/Overlay";
import { EMPTY_SECRET, MODE_LABEL, SecretSource, secretPayload, secretReady, type SecretDraft, type SecretMode } from "../../components/SecretSource";
import { formatDate } from "../../i18n";
import { errorText, useWsMutation, useWsQuery } from "../../lib";
import { Kpi } from "../OpsCharts";

interface Row { name: string; created_at: string; rotated_at: string | null; used_by: number; provider: "internal" | "vault"; vault_mode: SecretMode | ""; vault_mount: string; vault_path: string; vault_key: string; vault_version: number | null }
interface TestResult { ok: boolean; fields?: string[]; mode?: string; message?: string; code?: string }

/** Every secret of the workspace and where it lives: Fluxonata's own vault or HashiCorp Vault. */
export function SecretsTab() {
  const q = useWsQuery<{ results: Row[] }>("secrets", "/secrets/");
  const create = useWsMutation<Record<string, unknown>>("POST", "/secrets/", ["secrets", "vault-info"]);
  const replace = useWsMutation<{ name: string; value: string }>("PUT", (i) => `/secrets/${i.name}/`, ["secrets"]);
  const remove = useWsMutation<{ name: string; purge: boolean }>("DELETE", (i) => `/secrets/${i.name}/?purge=${i.purge ? 1 : 0}`, ["secrets"]);
  const test = useWsMutation<{ name: string }, TestResult>("POST", (i) => `/secrets/${i.name}/test/`);
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [src, setSrc] = useState<SecretDraft>(EMPTY_SECRET);
  const [rotating, setRotating] = useState<Row | null>(null);
  const [newValue, setNewValue] = useState("");
  const [toDelete, setToDelete] = useState<Row | null>(null);
  const [purge, setPurge] = useState(true);
  const [result, setResult] = useState<{ row: Row; res: TestResult } | null>(null);
  const all = q.data?.results ?? [];
  const rows = useMemo(() => all.filter((r) => `${r.name} ${r.vault_path}`.toLowerCase().includes(search.trim().toLowerCase())), [all, search]);
  const paged = usePaged(rows, 10);
  const inVault = all.filter((r) => r.provider === "vault").length;
  const writable = (r: Row) => r.provider === "internal" || r.vault_mode === "managed" || r.vault_mode === "reference";
  return (
    <>
      <ul className="kpis" aria-label="Chiffres des secrets">
        <Kpi label="Secrets" value={all.length} />
        <Kpi label="Dans le coffre de Fluxonata" value={all.length - inVault} />
        <Kpi label="Dans Vault" value={inVault} tone="info" hint="valeur jamais stockée ici" />
      </ul>
      <Section title="Secrets de l'espace" hint="Les connexions, les fournisseurs d'IA et les rappels signés lisent leurs secrets ici, au dernier moment et côté serveur. La valeur n'est jamais affichée : on peut la remplacer, pas la relire."
        actions={<button type="button" className="primary" onClick={() => { create.reset(); setName(""); setSrc(EMPTY_SECRET); setCreating(true); }}>Nouveau secret</button>}>
        <div className="adm-filters"><SearchBox label="Rechercher un secret" value={search} onChange={setSearch} placeholder="Nom ou chemin Vault" /><span className="lst-count" role="status">{rows.length} secret(s)</span></div>
        {(test.error || remove.error || replace.error) && <p role="alert" className="field-error">Erreur : {errorText(test.error ?? remove.error ?? replace.error)}</p>}
        <div className="table-wrap"><table className="adm-table"><caption className="sr-only">Secrets de l'espace</caption>
          <thead><tr><th scope="col">Secret</th><th scope="col">Où il est conservé</th><th scope="col">Utilisé par</th><th scope="col">Créé le</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{paged.slice.map((r) => (
            <tr key={r.name}>
              <td><strong className="mono">{r.name}</strong></td>
              <td>{r.provider === "vault"
                ? <><Badge tone="info">Vault</Badge> <span className="lst-sub">{MODE_LABEL[r.vault_mode as SecretMode] ?? r.vault_mode}</span><span className="lst-sub mono">{r.vault_mount}/{r.vault_path}{r.vault_key ? ` · ${r.vault_key}` : ""}{r.vault_version ? ` · v${r.vault_version}` : ""}</span></>
                : <Badge>Coffre de Fluxonata</Badge>}</td>
              <td>{r.used_by} connexion(s)</td>
              <td>{formatDate(r.created_at)}{r.rotated_at && <span className="lst-sub">remplacé le {formatDate(r.rotated_at)}</span>}</td>
              <td className="row-actions">
                {r.provider === "vault" && <button type="button" onClick={() => test.mutate({ name: r.name }, { onSuccess: (res) => setResult({ row: r, res }) })} aria-label={`Tester la lecture du secret ${r.name}`}>Tester</button>}
                {writable(r) && <button type="button" onClick={() => { replace.reset(); setNewValue(""); setRotating(r); }} aria-label={`Remplacer la valeur du secret ${r.name}`}>Remplacer</button>}
                <button type="button" className="danger" onClick={() => { remove.reset(); setPurge(true); setToDelete(r); }} aria-label={`Supprimer le secret ${r.name}`}>Supprimer</button>
              </td>
            </tr>))}
            {rows.length === 0 && <EmptyRow cols={5}>{q.isLoading ? "Chargement…" : all.length === 0 ? "Aucun secret : créez-en un, ou ajoutez une connexion." : "Aucun secret ne correspond."}</EmptyRow>}</tbody></table></div>
        {paged.pager("Pagination des secrets")}
      </Section>

      {creating && (
        <Overlay wide title="Nouveau secret" description="Choisissez où la valeur est conservée." onClose={() => setCreating(false)}
          footer={<><button type="button" onClick={() => setCreating(false)}>Annuler</button><button type="submit" form="sec-form" className="primary" disabled={!name || !secretReady(src) || create.isPending}>Créer le secret</button></>}>
          <form id="sec-form" onSubmit={(e) => { e.preventDefault(); create.mutate({ name, ...secretPayload(src) }, { onSuccess: () => setCreating(false) }); }}>
            <div className="field"><label htmlFor="sec-name">Nom du secret</label><input id="sec-name" value={name} pattern="[A-Za-z0-9_-]+" maxLength={60} onChange={(e) => setName(e.target.value)} required />
              <p className="hint">Lettres, chiffres, tirets : c'est le nom que les connexions et les fournisseurs d'IA utilisent.</p></div>
            <SecretSource value={src} onChange={setSrc} allowDynamic name="sec" internalLabel="Valeur du secret" />
            {create.error && <p role="alert" className="field-error">Erreur : {errorText(create.error)}</p>}
          </form>
        </Overlay>
      )}
      {rotating && (
        <Overlay variant="modal" title={`Remplacer « ${rotating.name} »`} description={rotating.provider === "vault" ? "La nouvelle valeur est écrite dans Vault (nouvelle version)." : "L'ancienne valeur est définitivement remplacée."} onClose={() => setRotating(null)}
          footer={<><button type="button" onClick={() => setRotating(null)}>Annuler</button><button type="submit" form="rot-form" className="primary" disabled={!newValue || replace.isPending}>Remplacer</button></>}>
          <form id="rot-form" onSubmit={(e) => { e.preventDefault(); replace.mutate({ name: rotating.name, value: newValue }, { onSuccess: () => setRotating(null) }); }}>
            <div className="field"><label htmlFor="rot-v">Nouvelle valeur</label><input id="rot-v" type="password" autoComplete="off" value={newValue} onChange={(e) => setNewValue(e.target.value)} required /></div>
            {replace.error && <p role="alert" className="field-error">Erreur : {errorText(replace.error)}</p>}
          </form>
        </Overlay>
      )}
      {toDelete && (
        <Confirm title="Supprimer ce secret ?" confirmLabel="Supprimer le secret" busy={remove.isPending} onCancel={() => setToDelete(null)}
          onConfirm={() => remove.mutate({ name: toDelete.name, purge }, { onSuccess: () => setToDelete(null) })}>
          <p>Le secret <strong>{toDelete.name}</strong> sera supprimé de Fluxonata. Il est refusé s'il est encore utilisé par une connexion.</p>
          {toDelete.provider === "vault" && toDelete.vault_mode === "managed" && (
            <div className="adm-rule"><input id="del-purge" className="adm-switch" type="checkbox" checked={purge} onChange={(e) => setPurge(e.target.checked)} /><div><label htmlFor="del-purge">Supprimer aussi la valeur dans Vault</label></div></div>
          )}
          {toDelete.provider === "vault" && toDelete.vault_mode !== "managed" && <p className="hint">Le secret dans Vault n'est pas touché : Fluxonata ne l'a pas écrit.</p>}
          {remove.error && <p role="alert" className="field-error">Erreur : {errorText(remove.error)}</p>}
        </Confirm>
      )}
      {result && (
        <Overlay variant="modal" title={`Lecture de « ${result.row.name} »`} onClose={() => setResult(null)} footer={<button type="button" className="primary" onClick={() => setResult(null)}>Fermer</button>}>
          {result.res.ok
            ? <><p role="status"><Badge tone="ok">✔ Lisible</Badge></p>{(result.res.fields?.length ?? 0) > 0 && <p>Champs présents dans Vault : {result.res.fields?.map((f) => <Badge key={f}>{f}</Badge>)}</p>}<p className="hint">Les valeurs ne sont jamais affichées.</p></>
            : <p role="alert" className="field-error"><Badge tone="bad">✖ Illisible</Badge> {result.res.message}</p>}
        </Overlay>
      )}
    </>
  );
}
