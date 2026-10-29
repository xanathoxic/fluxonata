import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type ApiError } from "../../api";
import { Badge, EmptyRow, Section } from "../../components/AdminUi";
import { Overlay } from "../../components/Overlay";
import { formatDate } from "../../i18n";
import { errorText } from "../../lib";
import { Kpi } from "../OpsCharts";

interface Cfg {
  address: string; namespace: string; kv_mount: string; base_prefix: string; auth_mount: string; role_id: string; secret_id_set: boolean; ca_set: boolean;
  verify_tls: boolean; enabled: boolean; last_test_ok: boolean | null; last_test_at: string | null; last_test_message: string; secrets: { internal: number; vault: number };
}
interface Step { id: string; label: string; ok: boolean; message: string; code?: string }
interface Grant { pattern: string; writable: boolean }
interface WsGrants { workspace: string; name: string; own_prefix: string; grants: Grant[]; secrets: number }

const GUIDE = `# 1. Activer la méthode AppRole
vault auth enable approle

# 2. Politique : ce que Fluxonata a le droit de faire dans Vault
vault policy write fluxonata - <<'POLITIQUE'
path "secret/data/fluxonata/*"     { capabilities = ["create", "read", "update", "delete"] }
path "secret/metadata/fluxonata/*" { capabilities = ["read", "delete", "list"] }
path "database/creds/*"            { capabilities = ["read"] }      # secrets dynamiques
path "sys/leases/revoke"           { capabilities = ["update"] }    # fin de bail
POLITIQUE

# 3. Le rôle, puis ses deux identifiants (à saisir ci-dessous)
vault write auth/approle/role/fluxonata token_policies=fluxonata token_ttl=1h
vault read -field=role_id auth/approle/role/fluxonata/role-id
vault write -f -field=secret_id auth/approle/role/fluxonata/secret-id`;

const VAULT_MOUNTS = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

export function VaultTab() {
  const qc = useQueryClient();
  const cfg = useQuery({ queryKey: ["platform", "vault"], queryFn: () => api<Cfg>("/platform/vault/") });
  const grants = useQuery({ queryKey: ["platform", "vault-grants"], queryFn: () => api<{ results: WsGrants[] }>("/platform/vault/grants/") });
  const [f, setF] = useState({ address: "", namespace: "", kv_mount: "secret", base_prefix: "fluxonata", auth_mount: "approle", role_id: "", secret_id: "", ca_pem: "", verify_tls: true, enabled: false });
  const [steps, setSteps] = useState<{ ok: boolean; steps: Step[] } | null>(null);
  const [guide, setGuide] = useState(false);
  const [editing, setEditing] = useState<WsGrants | null>(null);
  const [rows, setRows] = useState<Grant[]>([]);
  const [saved, setSaved] = useState(false);
  useEffect(() => { const c = cfg.data; if (c) setF({ address: c.address, namespace: c.namespace, kv_mount: c.kv_mount, base_prefix: c.base_prefix, auth_mount: c.auth_mount, role_id: c.role_id, secret_id: "", ca_pem: "", verify_tls: c.verify_tls, enabled: c.enabled }); }, [cfg.data]);
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["platform", "vault"] }); void qc.invalidateQueries({ queryKey: ["platform", "audit"] }); };
  const save = useMutation<Cfg, ApiError>({
    mutationFn: () => api<Cfg>("/platform/vault/", { method: "PUT", json: { ...f, ca_pem: f.ca_pem || undefined, secret_id: f.secret_id || undefined } }),
    onSuccess: () => { setSaved(true); refresh(); },
  });
  const test = useMutation<{ ok: boolean; steps: Step[] }, ApiError>({ mutationFn: () => api("/platform/vault/test/", { method: "POST" }), onSuccess: (r) => { setSteps(r); refresh(); } });
  const saveGrants = useMutation<unknown, ApiError, { workspace: string; grants: Grant[] }>({
    mutationFn: (i) => api(`/platform/vault/grants/${i.workspace}/`, { method: "PUT", json: { grants: i.grants } }),
    onSuccess: () => { setEditing(null); void qc.invalidateQueries({ queryKey: ["platform", "vault-grants"] }); void qc.invalidateQueries({ queryKey: ["platform", "audit"] }); },
  });
  const c = cfg.data;
  const set = (patch: Partial<typeof f>) => { setSaved(false); setF({ ...f, ...patch }); };
  const mountsOk = VAULT_MOUNTS.test(f.kv_mount) && VAULT_MOUNTS.test(f.auth_mount);
  return (
    <>
      <ul className="kpis" aria-label="État du coffre externe">
        <Kpi label="Coffre externe" value={c?.enabled ? "Activé" : c?.address ? "Désactivé" : "Non configuré"} tone={c?.enabled ? "ok" : undefined} />
        <Kpi label="Dernier test" value={c?.last_test_ok === null || c?.last_test_ok === undefined ? "—" : c.last_test_ok ? "✔ Réussi" : "✖ Échec"} tone={c?.last_test_ok === false ? "bad" : c?.last_test_ok ? "ok" : undefined} hint={c?.last_test_at ? formatDate(c.last_test_at) : "jamais testé"} />
        <Kpi label="Secrets dans Vault" value={c?.secrets.vault ?? 0} tone="info" />
        <Kpi label="Secrets dans Fluxonata" value={c?.secrets.internal ?? 0} />
      </ul>

      <Section title="Connexion à Vault" hint="Un seul coffre HashiCorp Vault pour toute la plate-forme, atteint par AppRole. Les espaces choisissent, secret par secret, entre le coffre de Fluxonata et celui-ci. Le secret_id est chiffré, jamais réaffiché."
        actions={<><button type="button" onClick={() => setGuide(true)}>Guide de mise en place</button><button type="button" disabled={!c?.address || test.isPending} onClick={() => { test.reset(); test.mutate(); }}>{test.isPending ? "Test en cours…" : "Tester la connexion"}</button></>}>
        {c?.last_test_message && <p className="hint" role="status">Dernier test : {c.last_test_message}</p>}
        <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <div className="adm-two">
            <div className="field"><label htmlFor="vt-addr">Adresse de Vault</label><input id="vt-addr" type="url" value={f.address} placeholder="https://vault.exemple.org:8200" onChange={(e) => set({ address: e.target.value })} required />
              <p className="hint">En https. Pour un Vault du réseau interne, l'opérateur doit aussi autoriser son adresse (ORCH_EGRESS_ALLOWLIST).</p></div>
            <div className="field"><label htmlFor="vt-ns">Espace de noms (Vault Enterprise, facultatif)</label><input id="vt-ns" value={f.namespace} onChange={(e) => set({ namespace: e.target.value })} /></div>
            <div className="field"><label htmlFor="vt-role">role_id de l'AppRole</label><input id="vt-role" className="mono" value={f.role_id} onChange={(e) => set({ role_id: e.target.value })} required autoComplete="off" /></div>
            <div className="field"><label htmlFor="vt-sid">secret_id de l'AppRole</label><input id="vt-sid" type="password" className="mono" value={f.secret_id} placeholder={c?.secret_id_set ? "••••••••  (inchangé)" : ""} onChange={(e) => set({ secret_id: e.target.value })} autoComplete="new-password" />
              <p className="hint">{c?.secret_id_set ? "Un secret_id est enregistré. Laissez vide pour le conserver." : "Requis pour activer le coffre."}</p></div>
            <div className="field"><label htmlFor="vt-kv">Point de montage KV v2</label><input id="vt-kv" value={f.kv_mount} onChange={(e) => set({ kv_mount: e.target.value })} aria-invalid={!VAULT_MOUNTS.test(f.kv_mount)} /></div>
            <div className="field"><label htmlFor="vt-prefix">Préfixe des secrets de Fluxonata</label><input id="vt-prefix" value={f.base_prefix} onChange={(e) => set({ base_prefix: e.target.value })} />
              <p className="hint">Chaque espace écrit sous <code>{f.kv_mount}/{f.base_prefix}/&lt;espace&gt;/</code> et nulle part ailleurs sans autorisation.</p></div>
            <div className="field"><label htmlFor="vt-auth">Point de montage AppRole</label><input id="vt-auth" value={f.auth_mount} onChange={(e) => set({ auth_mount: e.target.value })} aria-invalid={!VAULT_MOUNTS.test(f.auth_mount)} /></div>
            <div className="field"><label htmlFor="vt-ca">Autorité de certification (PEM, facultatif)</label><textarea id="vt-ca" rows={3} className="mono" value={f.ca_pem} placeholder={c?.ca_set ? "Une autorité est enregistrée : collez-en une pour la remplacer." : "-----BEGIN CERTIFICATE-----"} onChange={(e) => set({ ca_pem: e.target.value })} /></div>
          </div>
          <div className="adm-rule"><input id="vt-tls" className="adm-switch" type="checkbox" checked={f.verify_tls} onChange={(e) => set({ verify_tls: e.target.checked })} /><div><label htmlFor="vt-tls">Vérifier le certificat TLS de Vault</label><p className="hint">À ne décocher que pour un essai : sans vérification, rien ne prouve que c'est bien votre Vault.</p></div></div>
          <div className="adm-rule"><input id="vt-on" className="adm-switch" type="checkbox" checked={f.enabled} onChange={(e) => set({ enabled: e.target.checked })} /><div><label htmlFor="vt-on">Activer le coffre externe</label><p className="hint">Désactivé, les secrets déjà dans Vault ne peuvent plus être lus (les exécutions concernées échouent) ; les secrets internes ne sont pas touchés.</p></div></div>
          <div className="adm-savebar">
            <button type="submit" className="primary" disabled={save.isPending || !mountsOk}>Enregistrer</button>
            {saved && <span role="status" className="ok-text">✔ Enregistré : testez la connexion.</span>}
            {save.error && <span role="alert" className="field-error">Erreur : {errorText(save.error)}</span>}
          </div>
        </form>
      </Section>

      <Section title="Accès par espace" hint="Chaque espace atteint sa propre zone de Vault en lecture et en écriture. Ici, vous pouvez lui ouvrir d'autres chemins (un motif avec *), en lecture seule ou avec écriture : par exemple database/creds/* pour des identifiants dynamiques. La politique de l'AppRole reste le dernier mot.">
        <div className="table-wrap"><table className="adm-table"><caption className="sr-only">Accès au coffre externe par espace</caption>
          <thead><tr><th scope="col">Espace</th><th scope="col">Zone propre</th><th scope="col">Accès supplémentaires</th><th scope="col">Secrets dans Vault</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
          <tbody>{(grants.data?.results ?? []).map((w) => (
            <tr key={w.workspace}>
              <td><strong>{w.name}</strong><span className="lst-sub mono">{w.workspace}</span></td>
              <td className="mono">{w.own_prefix}*</td>
              <td>{w.grants.length ? <span className="adm-chips">{w.grants.map((g) => <Badge key={g.pattern} tone={g.writable ? "warn" : "muted"}>{g.pattern}{g.writable ? " · écriture" : ""}</Badge>)}</span> : <span className="hint">Aucun</span>}</td>
              <td>{w.secrets}</td>
              <td><button type="button" onClick={() => { saveGrants.reset(); setRows(w.grants.map((g) => ({ ...g }))); setEditing(w); }} aria-label={`Modifier les accès de l'espace ${w.name}`}>Modifier</button></td>
            </tr>))}
            {(grants.data?.results.length ?? 0) === 0 && <EmptyRow cols={5}>{grants.isLoading ? "Chargement…" : "Aucun espace."}</EmptyRow>}</tbody></table></div>
      </Section>

      {steps && (
        <Overlay variant="modal" title="Test de la connexion à Vault" description={steps.ok ? "Tout fonctionne." : "Un point bloque : lisez le message de l'étape en échec."} onClose={() => setSteps(null)} footer={<button type="button" className="primary" onClick={() => setSteps(null)}>Fermer</button>}>
          <ol className="vt-steps">{steps.steps.map((s) => <li key={s.id}><span className="vt-head"><Badge tone={s.ok ? "ok" : "bad"}>{s.ok ? "✔" : "✖"}</Badge><strong>{s.label}</strong></span><span className="lst-sub">{s.message}{s.code ? ` (${s.code})` : ""}</span></li>)}</ol>
        </Overlay>
      )}
      {guide && (
        <Overlay wide title="Mettre Vault en place pour Fluxonata" description="À lancer par la personne qui administre Vault. Adaptez les chemins à vos moteurs." onClose={() => setGuide(false)} footer={<button type="button" className="primary" onClick={() => setGuide(false)}>Fermer</button>}>
          <pre tabIndex={0} aria-label="Commandes Vault" className="adm-json">{GUIDE}</pre>
          <p className="hint">Le <code>secret_id</code> obtenu est un identifiant sensible : saisissez-le ici, il est chiffré par Fluxonata. Pensez à le renouveler selon votre politique (TTL du secret_id).</p>
        </Overlay>
      )}
      {editing && (
        <Overlay wide title={`Accès au coffre — ${editing.name}`} description={`Zone propre : ${editing.own_prefix}* (toujours en lecture et écriture).`} onClose={() => setEditing(null)}
          footer={<><button type="button" onClick={() => setEditing(null)}>Annuler</button><button type="button" className="primary" disabled={saveGrants.isPending} onClick={() => saveGrants.mutate({ workspace: editing.workspace, grants: rows.filter((r) => r.pattern.trim()).map((r) => ({ pattern: r.pattern.trim(), writable: r.writable })) })}>Enregistrer les accès</button></>}>
          {rows.length === 0 && <p className="hint">Aucun accès supplémentaire : cet espace ne voit que sa zone propre.</p>}
          {rows.map((r, i) => (
            <div className="vt-grant" key={i}>
              <div className="field"><label htmlFor={`vg-${i}`}>Motif {i + 1}</label><input id={`vg-${i}`} className="mono" value={r.pattern} placeholder="secret/equipe-a/*" onChange={(e) => setRows(rows.map((x, k) => (k === i ? { ...x, pattern: e.target.value } : x)))} /></div>
              <label className="ss-mode"><input type="checkbox" checked={r.writable} onChange={(e) => setRows(rows.map((x, k) => (k === i ? { ...x, writable: e.target.checked } : x)))} /> Écriture</label>
              <button type="button" onClick={() => setRows(rows.filter((_, k) => k !== i))} aria-label={`Retirer le motif ${i + 1}`}>Retirer</button>
            </div>
          ))}
          <button type="button" onClick={() => setRows([...rows, { pattern: "", writable: false }])}>Ajouter un motif</button>
          <p className="hint">Un motif commence par le point de montage, puis un chemin, avec <code>*</code> pour « n'importe quoi » : <code>secret/partage/*</code>, <code>database/creds/equipe-a-*</code>.</p>
          {saveGrants.error && <p role="alert" className="field-error">Erreur : {errorText(saveGrants.error)}</p>}
        </Overlay>
      )}
    </>
  );
}
