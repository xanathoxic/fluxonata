import { useState } from "react";
import { api, type Action, type ApiError, type Page, type WorkflowSummary } from "../../api";
import { useApp } from "../../context";
import { formatDate } from "../../i18n";
import { errorText, useWsMutation, useWsQuery } from "../../lib";
import { Badge, Confirm, EmptyRow, SearchBox, Section, usePaged, type Tone } from "../../components/AdminUi";
import { Overlay } from "../../components/Overlay";
import { Kpi } from "../OpsCharts";

interface Pkg { slug: string; name: string; version: string; checksum: string; origin: string; created_at: string; download_url: string; contents: { actions: number; workflows: number; forms?: number } }
interface Report { signature?: { status: string; signer: string | null }; package: { name: string; version: string; license: string } | null; items: { kind: string; slug: string; version: string; status: string; messages: string[] }[]; errors: string[]; applied: boolean; can_apply: boolean }
const SIG_TONE: Record<string, Tone> = { trusted: "ok", untrusted: "warn", invalid: "bad", unsigned: "warn" };
const SIG: Record<string, string> = { trusted: "✔ Signature valide d'un éditeur de confiance", untrusted: "⚠ Signature valide mais éditeur inconnu", invalid: "✖ Signature invalide (paquet modifié ?)", unsigned: "⚠ Paquet non signé" };
const STATUS_TONE: Record<string, Tone> = { new: "ok", identical: "muted", conflict: "bad", invalid: "bad" };
const STATUS: Record<string, string> = { new: "✚ Nouveau", identical: "＝ Identique (ignoré)", conflict: "✖ Conflit", invalid: "✖ Invalide" };

function Picker({ legend, items, selected, onToggle, onAll }: { legend: string; items: { key: string; label: string }[]; selected: string[]; onToggle: (k: string) => void; onAll: (all: boolean) => void }) {
  const [q, setQ] = useState("");
  const shown = items.filter((i) => i.label.toLowerCase().includes(q.trim().toLowerCase()));
  return (
    <fieldset className="pk-pick">
      <legend>{legend}</legend>
      <div className="pk-pick-bar">
        <SearchBox label={`Filtrer : ${legend}`} value={q} onChange={setQ} placeholder="Filtrer…" />
        <span className="hint">{selected.length} sélectionné(s) sur {items.length}</span>
        <button type="button" onClick={() => onAll(true)} disabled={items.length === 0}>Tout</button>
        <button type="button" onClick={() => onAll(false)} disabled={selected.length === 0}>Aucun</button>
      </div>
      <div className="pk-pick-list">
        {shown.map((i) => <div key={i.key} className="field field-inline"><input id={`pk-${legend}-${i.key}`} type="checkbox" checked={selected.includes(i.key)} onChange={() => onToggle(i.key)} /><label htmlFor={`pk-${legend}-${i.key}`}>{i.label}</label></div>)}
        {shown.length === 0 && <p className="hint">Rien à afficher.</p>}
      </div>
    </fieldset>
  );
}

export function PackagesTab() {
  const { workspace, can } = useApp();
  const pkgs = useWsQuery<Page<Pkg>>("packages", "/packages/");
  const actions = useWsQuery<Page<Action>>("actions-lib", "/actions/");
  const wfs = useWsQuery<Page<WorkflowSummary>>("workflows", "/workflows/");
  const trusted = useWsQuery<{ results: unknown[] }>("trusted", "/packages/trusted-keys/", { enabled: can("administer") });
  const exp = useWsMutation<Record<string, unknown>>("POST", "/packages/export/", ["packages"]);
  const [f, setF] = useState({ name: "", slug: "", version: "1.0.0", description: "", sign: true });
  const [selA, setSelA] = useState<string[]>([]);
  const [selW, setSelW] = useState<string[]>([]);
  const [selF, setSelF] = useState<string[]>([]);
  const forms = useWsQuery<{ results: { slug: string; name: string; is_template: boolean; latest_version: { version: string } | null }[] }>("form-library", "/form-library/");
  const fItems = (forms.data?.results ?? []).filter((x) => !x.is_template && x.latest_version).map((x) => ({ key: `${x.slug}@${x.latest_version?.version}`, label: `${x.name} (v${x.latest_version?.version})` }));
  const [file, setFile] = useState<File | null>(null);
  const [strategy, setStrategy] = useState("abort");
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [dlg, setDlg] = useState<"export" | "import" | null>(null);
  const [search, setSearch] = useState("");
  const [created, setCreated] = useState("");
  const rows = (pkgs.data?.results ?? []).filter((p) => `${p.name} ${p.slug} ${p.version}`.toLowerCase().includes(search.trim().toLowerCase()));
  const paged = usePaged(rows, 10);
  const all = pkgs.data?.results ?? [];

  const toggle = (list: string[], set: (l: string[]) => void, v: string) => set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const aItems = (actions.data?.results ?? []).filter((a) => a.latest_version).map((a) => ({ key: `${a.slug}@${a.latest_version?.version}`, label: `${a.name} (v${a.latest_version?.version})` }));
  const wItems = (wfs.data?.results ?? []).filter((w) => w.latest_version).map((w) => ({ key: `${w.slug}@${w.latest_version?.version}`, label: `${w.name} (v${w.latest_version?.version})` }));
  const runImport = async (mode: "preview" | "apply") => {
    if (!file) return;
    setBusy(true); setErr("");
    const form = new FormData();
    form.append("file", file); form.append("mode", mode); form.append("strategy", strategy);
    try { setReport(await api<Report>(`/workspaces/${workspace.slug}/packages/import/`, { form })); }
    catch (e) { const a = e as ApiError; setReport((a.details as unknown as Report | null)?.items ? (a.details as unknown as Report) : null); setErr(a.message); }
    finally { setBusy(false); }
  };
  const admin = can("administer");
  if (!can("publish")) return <p>Vous pouvez consulter les paquets existants ; export et import sont réservés aux concepteurs.</p>;
  return (
    <>
      <ul className="kpis" aria-label="Chiffres des paquets">
        <Kpi label="Paquets" value={all.length} />
        <Kpi label="Exportés" value={all.filter((p) => p.origin !== "import").length} tone="info" />
        <Kpi label="Importés" value={all.filter((p) => p.origin === "import").length} />
        {admin && <Kpi label="Éditeurs de confiance" value={trusted.data?.results.length ?? 0} tone="ok" />}
      </ul>
      <Section title="Paquets de l'espace" hint="Un paquet regroupe actions et workflows avec un manifeste et des sommes de contrôle. Il ne contient jamais de secret et son import ne lance aucun code."
        actions={<><button type="button" onClick={() => { setReport(null); setErr(""); setFile(null); setDlg("import"); }}>Importer un paquet</button><button type="button" className="primary" onClick={() => { exp.reset(); setDlg("export"); }}>Exporter un paquet</button></>}>
        {created && <p role="status" className="ok-text">✔ Paquet créé : téléchargez-le dans la liste ci-dessous.</p>}
        <div className="adm-filters"><SearchBox label="Rechercher un paquet" value={search} onChange={setSearch} placeholder="Nom, identifiant ou version" /><span className="lst-count" role="status">{rows.length} paquet(s)</span></div>
        <div className="table-wrap"><table className="adm-table"><caption className="sr-only">Paquets de l'espace</caption>
          <thead><tr><th scope="col">Paquet</th><th scope="col">Version</th><th scope="col">Contenu</th><th scope="col">Origine</th><th scope="col">Date</th><th scope="col">Téléchargement</th></tr></thead>
          <tbody>{paged.slice.map((p) => (
            <tr key={`${p.slug}-${p.version}`}>
              <td><strong>{p.name}</strong><span className="lst-sub mono">{p.slug}</span></td><td><Badge tone="info">{p.version}</Badge></td>
              <td>{p.contents.actions} action(s), {p.contents.workflows} workflow(s){p.contents.forms ? `, ${p.contents.forms} formulaire(s)` : ""}</td>
              <td>{p.origin === "import" ? <Badge>Importé</Badge> : <Badge tone="ok">Exporté</Badge>}</td><td>{formatDate(p.created_at)}</td>
              <td><a className="button" href={p.download_url} download>{p.slug}-{p.version}.zip</a></td>
            </tr>))}
            {rows.length === 0 && <EmptyRow cols={6}>{pkgs.isLoading ? "Chargement…" : all.length === 0 ? "Aucun paquet : exportez des actions et workflows, ou importez une archive." : "Aucun paquet ne correspond."}</EmptyRow>}</tbody></table></div>
        {paged.pager("Pagination des paquets")}
      </Section>
      {admin && <TrustedKeys />}

      {dlg === "export" && (
        <Overlay wide title="Exporter un paquet" description="Choisissez ce qu'il contient : seules les versions publiées sont incluses." onClose={() => setDlg(null)}
          footer={<><button type="button" onClick={() => setDlg(null)}>Annuler</button><button type="submit" form="pk-form" className="primary" disabled={exp.isPending || (!selA.length && !selW.length && !selF.length)}>Créer le paquet</button></>}>
          <form id="pk-form" onSubmit={(e) => { e.preventDefault(); exp.mutate({ ...f, actions: selA.map((s) => ({ slug: s.split("@")[0], version: s.split("@")[1] })), workflows: selW.map((s) => ({ slug: s.split("@")[0], version: s.split("@")[1] })), forms: selF.map((s) => ({ slug: s.split("@")[0], version: s.split("@")[1] })) }, { onSuccess: () => { setCreated(f.name); setDlg(null); } }); }}>
            <div className="adm-two">
              <div className="field"><label htmlFor="pk-n">Nom</label><input id="pk-n" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></div>
              <div className="field"><label htmlFor="pk-s">Identifiant</label><input id="pk-s" value={f.slug} pattern="[A-Za-z0-9_-]+" onChange={(e) => setF({ ...f, slug: e.target.value })} required /></div>
              <div className="field"><label htmlFor="pk-v">Version</label><input id="pk-v" value={f.version} pattern="\d+\.\d+\.\d+" onChange={(e) => setF({ ...f, version: e.target.value })} required /></div>
            </div>
            <div className="adm-rule"><input id="pk-sign" className="adm-switch" type="checkbox" checked={f.sign} onChange={(e) => setF({ ...f, sign: e.target.checked })} /><div><label htmlFor="pk-sign">Signer le paquet (preuve de provenance)</label></div></div>
            <Picker legend="Actions à inclure" items={aItems} selected={selA} onToggle={(k) => toggle(selA, setSelA, k)} onAll={(on) => setSelA(on ? aItems.map((i) => i.key) : [])} />
            <Picker legend="Workflows à inclure (versions publiées)" items={wItems} selected={selW} onToggle={(k) => toggle(selW, setSelW, k)} onAll={(on) => setSelW(on ? wItems.map((i) => i.key) : [])} />
            <Picker legend="Formulaires à inclure (versions publiées)" items={fItems} selected={selF} onToggle={(k) => toggle(selF, setSelF, k)} onAll={(on) => setSelF(on ? fItems.map((i) => i.key) : [])} />
            {exp.error && <p role="alert" className="field-error">Erreur : {errorText(exp.error)}</p>}
          </form>
        </Overlay>
      )}

      {dlg === "import" && (
        <Overlay wide title="Importer un paquet" description="Un aperçu montre ce qui serait créé ou en conflit avant de modifier quoi que ce soit." onClose={() => setDlg(null)}
          footer={<><button type="button" onClick={() => setDlg(null)}>Fermer</button>
            <button type="button" onClick={() => void runImport("preview")} disabled={!file || busy}>Aperçu (rien n'est modifié)</button>
            <button type="button" className="primary" onClick={() => void runImport("apply")} disabled={!file || busy || (report !== null && !report.can_apply)}>Importer</button></>}>
          <div className="field pk-drop"><label htmlFor="imp-f">Archive de paquet (.zip, 5 Mo maximum)</label><input id="imp-f" type="file" accept=".zip,application/zip" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setReport(null); }} />
            {file && <p className="hint">{file.name} · {(file.size / 1024).toFixed(0)} Ko</p>}</div>
          <div className="field"><label htmlFor="imp-s">En cas de conflit</label>
            <select id="imp-s" value={strategy} onChange={(e) => setStrategy(e.target.value)}><option value="abort">Tout annuler</option><option value="skip">Ignorer les éléments en conflit</option></select></div>
          {err && !report && <p role="alert" className="field-error">Erreur : {err}</p>}
          {report && (
            <section aria-label="Rapport d'import" className="adm-card pk-report">
              <h3>{report.package ? `${report.package.name} ${report.package.version}` : "Archive refusée"}{report.package && <> <Badge>licence {report.package.license}</Badge></>}</h3>
              {report.signature && <p><Badge tone={SIG_TONE[report.signature.status] ?? "muted"}>{SIG[report.signature.status]}</Badge>{report.signature.signer ? ` ${report.signature.signer}` : ""}</p>}
              <p role="status"><strong>{report.applied ? "✔ Import effectué." : report.can_apply ? "✔ Aucun blocage : vous pouvez importer." : "✖ Import bloqué."}</strong></p>
              {report.errors.map((e) => <p key={e} role="alert" className="field-error">Erreur : {e}</p>)}
              <ul className="plain pk-items">{report.items.map((i) => <li key={`${i.kind}${i.slug}${i.version}`}><Badge tone={STATUS_TONE[i.status] ?? "muted"}>{STATUS[i.status]}</Badge> {i.kind === "action" ? "action" : "workflow"} <strong>{i.slug}</strong> {i.version} <span className="hint">{i.messages.join(" ")}</span></li>)}</ul>
            </section>
          )}
        </Overlay>
      )}
    </>
  );
}

function TrustedKeys() {
  const q = useWsQuery<{ instance_public_key: string; instance_key_id: string; results: { id: number; name: string; key_id: string }[] }>("trusted", "/packages/trusted-keys/");
  const add = useWsMutation<{ name: string; public_key: string }>("POST", "/packages/trusted-keys/", ["trusted"]);
  const del = useWsMutation<{ id: number }>("DELETE", (i) => `/packages/trusted-keys/${i.id}/`, ["trusted"]);
  const [k, setK] = useState({ name: "", public_key: "" });
  const [open, setOpen] = useState(false);
  const [toDel, setToDel] = useState<{ id: number; name: string } | null>(null);
  return (
    <Section title="Éditeurs de confiance" hint={<>Clé publique de cette instance (à communiquer aux autres instances pour qu'elles fassent confiance à vos paquets) : <code>{q.data?.instance_public_key}</code></>}
      actions={<button type="button" onClick={() => { add.reset(); setOpen(true); }}>Ajouter un éditeur</button>}>
      <div className="table-wrap"><table className="adm-table"><caption className="sr-only">Éditeurs de confiance</caption>
        <thead><tr><th scope="col">Éditeur</th><th scope="col">Empreinte de la clé</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
        <tbody>{q.data?.results.map((r) => <tr key={r.id}><td><strong>{r.name}</strong></td><td className="mono">{r.key_id}</td><td><button type="button" onClick={() => setToDel({ id: r.id, name: r.name })} aria-label={`Ne plus faire confiance à ${r.name}`}>Retirer</button></td></tr>)}
          {(q.data?.results.length ?? 0) === 0 && <EmptyRow cols={3}>Aucun éditeur de confiance : seuls vos propres paquets sont reconnus comme signés par vous.</EmptyRow>}</tbody></table></div>
      {toDel && <Confirm title="Ne plus faire confiance à cet éditeur ?" confirmLabel="Retirer l'éditeur" busy={del.isPending} onCancel={() => setToDel(null)} onConfirm={() => { del.mutate({ id: toDel.id }); setToDel(null); }}><p>Les paquets signés par <strong>{toDel.name}</strong> ne seront plus reconnus comme venant d'un éditeur de confiance.</p></Confirm>}
      {open && (
        <Overlay variant="modal" title="Ajouter un éditeur de confiance" description="Collez la clé publique que cet éditeur vous a communiquée." onClose={() => setOpen(false)}
          footer={<><button type="button" onClick={() => setOpen(false)}>Annuler</button><button type="submit" form="tk-form" className="primary">Faire confiance</button></>}>
          <form id="tk-form" onSubmit={(e) => { e.preventDefault(); add.mutate(k, { onSuccess: () => { setK({ name: "", public_key: "" }); setOpen(false); } }); }}>
            <div className="field"><label htmlFor="tk-n">Nom de l'éditeur</label><input id="tk-n" value={k.name} onChange={(e) => setK({ ...k, name: e.target.value })} required /></div>
            <div className="field"><label htmlFor="tk-k">Clé publique (base64)</label><input id="tk-k" className="mono" value={k.public_key} onChange={(e) => setK({ ...k, public_key: e.target.value })} required /></div>
            {add.error && <p role="alert" className="field-error">Erreur : {errorText(add.error)}</p>}
          </form>
        </Overlay>
      )}
    </Section>
  );
}
