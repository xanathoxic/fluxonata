import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type ApiError } from "../../api";
import { Badge, Confirm, EmptyRow } from "../../components/AdminUi";
import { HelpTip } from "../../components/HelpTip";
import { Overlay } from "../../components/Overlay";
import { formatDate } from "../../i18n";
import { errorText } from "../../lib";

interface Host { id: number; host: string; note: string; created_by: string; created_at: string }
interface Egress { environment: string[]; results: Host[] }

/** Internal hosts (an API on this machine, a service of the company network) that the platform's server may call. Everything else
 * internal stays refused. Platform administrators only; the server checks it again and audits each change. */
export function NetworkTab() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["platform", "egress"], queryFn: () => api<Egress>("/platform/egress/") });
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ host: "", note: "", sure: false });
  const [remove, setRemove] = useState<Host | null>(null);
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["platform", "egress"] }); void qc.invalidateQueries({ queryKey: ["platform", "audit"] }); };
  const add = useMutation<unknown, ApiError>({ mutationFn: () => api("/platform/egress/", { json: { host: f.host, note: f.note } }), onSuccess: () => { setOpen(false); refresh(); } });
  const del = useMutation<unknown, ApiError, Host>({ mutationFn: (h) => api(`/platform/egress/${h.id}/`, { method: "DELETE" }), onSuccess: () => { setRemove(null); refresh(); } });
  const rows = list.data?.results ?? [];
  const env = list.data?.environment ?? [];
  return (
    <>
      <div className="toolbar-row">
        <span style={{ flex: 1 }}><HelpTip caption="À savoir">Par sécurité, le serveur refuse d'appeler une adresse interne (cette machine, un réseau privé) : une connexion, une liste dynamique ou un contrôle ne peuvent pas s'en servir pour atteindre un service interne. Autorisez ici, une par une, les adresses internes que vos connexions doivent joindre. Une adresse = un nom d'hôte ou une IP exacts (pas de plage ni de joker). Les métadonnées cloud et les adresses de lien local ne peuvent jamais être autorisées. Le changement s'applique en quelques secondes.</HelpTip></span>
        <button type="button" className="primary" onClick={() => { add.reset(); setF({ host: "", note: "", sure: false }); setOpen(true); }}>Autoriser une adresse</button>
      </div>
      <section className="adm-card">
        <div className="table-wrap">
          <table className="adm-table">
            <caption className="sr-only">Adresses internes autorisées</caption>
            <thead><tr><th scope="col">Adresse</th><th scope="col">Origine</th><th scope="col">Note</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {env.map((h) => (
                <tr key={`env-${h}`}><td><code>{h}</code></td><td><Badge tone="muted">Configuration du serveur</Badge></td><td className="hint">Définie par l'opérateur (ORCH_EGRESS_ALLOWLIST), non modifiable ici</td><td /></tr>
              ))}
              {rows.map((h) => (
                <tr key={h.id}><td><code>{h.host}</code></td><td>Ajoutée par {h.created_by || "—"} le {formatDate(h.created_at)}</td><td>{h.note}</td>
                  <td><button type="button" onClick={() => { del.reset(); setRemove(h); }} aria-label={`Retirer l'adresse ${h.host}`}>Retirer</button></td></tr>
              ))}
              {env.length + rows.length === 0 && <EmptyRow cols={4}>Aucune adresse interne autorisée : seules les adresses publiques peuvent être appelées.</EmptyRow>}
            </tbody>
          </table>
        </div>
      </section>
      {open && (
        <Overlay variant="modal" title="Autoriser une adresse interne" onClose={() => setOpen(false)}
          description="Le serveur de la plate-forme (et ses workers) pourra appeler cette adresse, pour toutes les connexions de tous les espaces."
          footer={<><button type="button" onClick={() => setOpen(false)}>Annuler</button><button type="submit" form="egress-form" className="primary" disabled={!f.sure || !f.host.trim() || add.isPending}>Autoriser</button></>}>
          <form id="egress-form" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
            <div className="field"><label htmlFor="eg-host">Nom d'hôte ou adresse IP</label><input id="eg-host" value={f.host} onChange={(e) => setF({ ...f, host: e.target.value })} placeholder="127.0.0.1 ou api.interne.exemple" autoComplete="off" required />
              <p className="hint">Sans « http:// », sans port, sans chemin, sans joker. Le port reste libre.</p></div>
            <div className="field"><label htmlFor="eg-note">Note (facultatif)</label><input id="eg-note" value={f.note} maxLength={200} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="À quoi sert cette adresse ?" /></div>
            <div className="field field-inline"><input id="eg-sure" type="checkbox" checked={f.sure} onChange={(e) => setF({ ...f, sure: e.target.checked })} /><label htmlFor="eg-sure">Je confirme que les concepteurs d'actions et de connexions peuvent faire appeler cette adresse par le serveur.</label></div>
            {add.error && <p role="alert" className="field-error">Erreur : {errorText(add.error)}</p>}
          </form>
        </Overlay>
      )}
      {remove && (
        <Confirm title={`Retirer l'adresse « ${remove.host} » ?`} confirmLabel="Retirer" busy={del.isPending} onConfirm={() => del.mutate(remove)} onCancel={() => setRemove(null)}>
          Les connexions et les listes qui l'utilisent cesseront de fonctionner dans quelques secondes.
          {del.error && <span role="alert" className="field-error"> Erreur : {errorText(del.error)}</span>}
        </Confirm>
      )}
    </>
  );
}
