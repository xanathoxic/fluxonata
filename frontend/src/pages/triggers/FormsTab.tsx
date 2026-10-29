import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { apiAll, type WorkflowSummary } from "../../api";
import { Badge, EmptyRow, SearchBox, usePaged } from "../../components/AdminUi";
import { Overlay } from "../../components/Overlay";
import { RowIcon } from "../../components/RowIcon";
import { useApp } from "../../context";
import { errorText, useWsMutation, useWsQuery } from "../../lib";
import { HelpTip } from "../../components/HelpTip";

interface FormRow { id: number; name: string; title: string; workflow: string; environment: string; enabled: boolean; url: string; max_per_hour: number; submissions_last_hour: number; expires_at: string | null; max_uses: number | null; uses: number; state: "active" | "disabled" | "expired" | "exhausted" }
const EMPTY = { name: "", title: "", description: "", workflow: "", environment: "test", max_per_hour: 60, expires_local: "", max_uses: "" };
const STATE: Record<string, string> = { active: "Disponible", disabled: "⏸ Désactivé", expired: "⌛ Expiré", exhausted: "🚫 Limite d'utilisations atteinte" };
/** `<input type="datetime-local">` works in local time without a zone: send an absolute instant, show it back in local time. */
const toIso = (local: string) => (local ? new Date(local).toISOString() : null);
const toLocal = (iso: string | null) => (iso ? new Date(new Date(iso).getTime() - new Date(iso).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : "");
const when = (iso: string) => new Date(iso).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });

function Availability({ id, local, uses, setLocal, setUses, hint }: { id: string; local: string; uses: string; setLocal: (v: string) => void; setUses: (v: string) => void; hint?: string }) {
  return (
    <fieldset>
      <legend>Disponibilité</legend>
      <div className="field">
<HelpTip>Après cette date, le formulaire se désactive de lui-même (heure locale de votre navigateur). Vide : sans limite de durée.</HelpTip><label htmlFor={`${id}-x`}>Disponible jusqu'au (facultatif)</label><input id={`${id}-x`} type="datetime-local" value={local} onChange={(e) => setLocal(e.target.value)} /></div>
      <div className="field">
<HelpTip>Chaque envoi accepté compte pour une utilisation ; au dernier, le formulaire se désactive. {hint}</HelpTip><label htmlFor={`${id}-u`}>Nombre d'utilisations maximum (facultatif)</label><input id={`${id}-u`} type="number" min={1} max={1000000} value={uses} onChange={(e) => setUses(e.target.value)} placeholder="Illimité" /></div>
    </fieldset>
  );
}

/** Public forms: a page anyone with the address can fill in to start a workflow. */
export function FormsTab() {
  const { can, workspace } = useApp();
  const edit = can("publish");
  const wfs = useQuery({ queryKey: ["workflows", workspace.slug], queryFn: () => apiAll<WorkflowSummary>(`/workspaces/${workspace.slug}/workflows/`) });
  const q = useWsQuery<{ results: FormRow[] }>("forms", "/forms/");
  const add = useWsMutation<Record<string, unknown>, FormRow>("POST", "/forms/", ["forms"]);
  const toggle = useWsMutation<{ id: number; enabled: boolean }>("PATCH", (i) => `/forms/${i.id}/`, ["forms"]);
  const avail = useWsMutation<{ id: number; expires_at: string | null; max_uses: number | null; reset_uses: boolean; enabled?: boolean }>("PATCH", (i) => `/forms/${i.id}/`, ["forms"]);
  const del = useWsMutation<{ id: number }>("DELETE", (i) => `/forms/${i.id}/`, ["forms"]);
  const [open, setOpen] = useState(false);
  const [f, setF] = useState(EMPTY);
  const [editing, setEditing] = useState<FormRow | null>(null);
  const [ed, setEd] = useState({ local: "", uses: "", reset: false });
  const openEdit = (r: FormRow) => { avail.reset(); setEd({ local: toLocal(r.expires_at), uses: r.max_uses?.toString() ?? "", reset: false }); setEditing(r); };
  const publishable = (wfs.data?.results ?? []).filter((w) => w.latest_version);
  const [search, setSearch] = useState("");
  const rows = (q.data?.results ?? []).filter((r) => `${r.name} ${r.title} ${r.workflow}`.toLowerCase().includes(search.trim().toLowerCase()));
  const paged = usePaged(rows, 10);
  const link = (u: string) => `${window.location.origin}${u}`;
  return (
    <>
      <p className="help-row"><HelpTip caption="À savoir">Une page publique, sans connexion, qui démarre un workflow avec les valeurs saisies. L'adresse est secrète (jeton long) ; chaque formulaire a un plafond d'envois par heure et un piège anti-robots. Le workflow tourne avec vos droits : si vous perdez le droit de lancer, le formulaire s'arrête.</HelpTip></p>
      {edit && <p><button type="button" className="primary" onClick={() => { add.reset(); setF(EMPTY); setOpen(true); }}>Nouveau formulaire public</button></p>}
      <section className="adm-card">
      <div className="adm-filters"><SearchBox label="Rechercher un formulaire" value={search} onChange={setSearch} placeholder="Nom, titre ou workflow" /><span className="lst-count" role="status">{rows.length} formulaire(s)</span></div>
      <div className="table-wrap"><table className="adm-table"><caption className="sr-only">Formulaires publics</caption>
        <thead><tr><th scope="col">Formulaire</th><th scope="col">Workflow</th><th scope="col">Adresse publique</th><th scope="col">État et disponibilité</th><th scope="col">Envois (dernière heure)</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>{paged.slice.map((r) => (
          <tr key={r.id}><td>{r.title}<br /><span className="hint">{r.name}</span></td><td>{r.workflow} ({r.environment})</td><td><a href={r.url}><code>{link(r.url)}</code></a></td>
            <td><Badge tone={r.state === "active" ? "ok" : r.state === "disabled" ? "muted" : "bad"}>{STATE[r.state]}</Badge>
              <br /><span className="hint">{r.expires_at ? `Jusqu'au ${when(r.expires_at)}` : "Sans limite de durée"}</span>
              <br /><span className="hint">{r.max_uses === null ? `${r.uses} utilisation(s)` : `${r.uses} / ${r.max_uses} utilisation(s)`}</span></td>
            <td>{r.submissions_last_hour} / {r.max_per_hour}</td>
            <td>{edit && <><button type="button" onClick={() => openEdit(r)} aria-label={`Modifier la disponibilité de ${r.name}`}>Disponibilité</button>{" "}<button type="button" onClick={() => toggle.mutate({ id: r.id, enabled: !r.enabled })} aria-label={`${r.enabled ? "Désactiver" : "Activer"} le formulaire ${r.name}`}>{r.enabled ? "Désactiver" : "Activer"}</button>{" "}<button type="button" className="icon-btn danger-icon" onClick={() => del.mutate({ id: r.id })} aria-label={`Supprimer le formulaire ${r.name}`} title="Supprimer"><RowIcon name="trash" /></button></>}</td></tr>))}
          {rows.length === 0 && <EmptyRow cols={6}>{q.data?.results.length ? "Aucun formulaire ne correspond." : "Aucun formulaire public."}</EmptyRow>}</tbody></table></div>
      {paged.pager("Pagination des formulaires")}
      </section>
      {edit && open && (
        <Overlay title="Nouveau formulaire public" description="Les champs viennent des entrées du workflow (et de son formulaire en étapes s'il en a un)." onClose={() => setOpen(false)}
          footer={add.data ? <button type="button" className="primary" onClick={() => setOpen(false)}>Fermer</button> : <><button type="button" onClick={() => setOpen(false)}>Annuler</button><button type="submit" form="pf-form" className="primary">Créer le formulaire</button></>}>
          {add.data ? <div role="status" className="onetime"><strong>Formulaire créé.</strong><br />Adresse à diffuser : <code>{link(add.data.url)}</code></div> : (
            <form id="pf-form" onSubmit={(e) => { e.preventDefault(); const { expires_local, max_uses, ...rest } = f; add.mutate({ ...rest, expires_at: toIso(expires_local), max_uses: max_uses ? Number(max_uses) : null }); }}>
              <div className="field"><label htmlFor="pf-n">Nom interne</label><input id="pf-n" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></div>
              <div className="field"><label htmlFor="pf-t">Titre affiché</label><input id="pf-t" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} required /></div>
              <div className="field"><label htmlFor="pf-d">Texte d'introduction</label><textarea id="pf-d" rows={3} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></div>
              <div className="field"><label htmlFor="pf-w">Workflow</label><select id="pf-w" value={f.workflow} onChange={(e) => setF({ ...f, workflow: e.target.value })} required><option value="">Choisir…</option>{publishable.map((w) => <option key={w.slug} value={w.slug}>{w.name}</option>)}</select></div>
              <div className="field"><label htmlFor="pf-e">Environnement</label><select id="pf-e" value={f.environment} onChange={(e) => setF({ ...f, environment: e.target.value })}><option value="test">Recette</option><option value="prod">Production</option></select></div>
              <div className="field"><label htmlFor="pf-m">Envois maximum par heure</label><input id="pf-m" type="number" min={1} max={1000} value={f.max_per_hour} onChange={(e) => setF({ ...f, max_per_hour: Number(e.target.value) })} /></div>
              <Availability id="pf" local={f.expires_local} uses={f.max_uses} setLocal={(v) => setF({ ...f, expires_local: v })} setUses={(v) => setF({ ...f, max_uses: v })} />
              {add.error && <p role="alert" className="field-error">Erreur : {errorText(add.error)}</p>}
            </form>)}
        </Overlay>
      )}
      {edit && editing && (
        <Overlay variant="modal" title={`Disponibilité de « ${editing.name} »`} description={`${STATE[editing.state]} · ${editing.uses} utilisation(s) jusqu'ici.`} onClose={() => setEditing(null)}
          footer={<><button type="button" onClick={() => setEditing(null)}>Annuler</button><button type="submit" form="pf-avail" className="primary" disabled={avail.isPending}>Enregistrer</button></>}>
          <form id="pf-avail" onSubmit={(e) => { e.preventDefault(); const reopen = editing.state === "expired" || editing.state === "exhausted"; avail.mutate({ id: editing.id, expires_at: toIso(ed.local), max_uses: ed.uses ? Number(ed.uses) : null, reset_uses: ed.reset, ...(reopen ? { enabled: true } : {}) }, { onSuccess: () => setEditing(null) }); }}>
            <Availability id="pf-e" local={ed.local} uses={ed.uses} setLocal={(v) => setEd({ ...ed, local: v })} setUses={(v) => setEd({ ...ed, uses: v })} hint={editing.state === "expired" || editing.state === "exhausted" ? "Enregistrer avec une nouvelle limite réactive le formulaire." : undefined} />
            <div className="field field-inline"><input id="pf-reset" type="checkbox" checked={ed.reset} onChange={(e) => setEd({ ...ed, reset: e.target.checked })} /><label htmlFor="pf-reset">Remettre le compteur d'utilisations à zéro</label></div>
            {avail.error && <p role="alert" className="field-error">Erreur : {errorText(avail.error)}</p>}
          </form>
        </Overlay>
      )}
    </>
  );
}
