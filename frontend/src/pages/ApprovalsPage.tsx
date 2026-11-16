import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type ApiError, type Approval, type Page } from "../api";
import { Fields } from "../components/Fields";
import { Overlay } from "../components/Overlay";
import { PageHeader } from "../components/PageHeader";
import { Tabs } from "../components/Tabs";
import { usePaged } from "../components/AdminUi";
import { useApp } from "../context";
import { formatDate } from "../i18n";
import { errorText, useWsMutation, useWsQuery } from "../lib";
import { HelpTip } from "../components/HelpTip";
import { Markdown } from "../components/Markdown";

/** The request itself: who asks, about what, and the body its author wrote in Markdown. */
function RequestTab({ ap }: { ap: Approval }) {
  return (
    <div className="ap-request">
      <dl className="ap-meta">
        <div><dt>Titre de la demande</dt><dd>{ap.title}</dd></div>
        <div><dt>Demandeur</dt><dd>{ap.requested_by ?? "—"}</dd></div>
        <div><dt>Workflow</dt><dd>{ap.workflow}</dd></div>
        <div><dt>Date</dt><dd>{formatDate(ap.created_at)}</dd></div>
        {ap.subject && <div className="ap-subject"><dt>Objet</dt><dd>{ap.subject}</dd></div>}
      </dl>
      {ap.body ? <Markdown source={ap.body} /> : ap.instructions ? <Markdown source={ap.instructions} /> : <p className="hint">Aucun détail n'accompagne cette demande.</p>}
    </div>
  );
}

function ContextTab({ ap }: { ap: Approval }) {
  return (
    <>
      <p className="hint">Les valeurs avec lesquelles le workflow a été lancé.</p>
      <Fields data={ap.context} />
      <p><Link to={`/runs/${ap.run_id}`}>Voir l'exécution</Link></p>
    </>
  );
}

/** The decision area, shown whatever tab is open: the comment, what blocks the decision, and the buttons. */
function DecisionBar({ ap, canApprove, comment, setComment, error, busy, decide }: { ap: Approval; canApprove: boolean; comment: string; setComment: (v: string) => void; error: ApiError | null; busy: boolean; decide: (approve: boolean) => void }) {
  const id = `c-${ap.id}`;
  if (ap.status !== "pending") {
    return <p className="ap-done"><strong>{ap.status === "approved" ? "✔ Approuvée" : "✖ Refusée"}</strong> par {ap.decided_by} le {formatDate(ap.decided_at)}{ap.comment ? ` — « ${ap.comment} »` : ""}</p>;
  }
  if (!canApprove) return <p className="hint">Votre rôle ne permet pas de décider.</p>;
  return (
    <div className="ap-decision">
      <div className="field"><label htmlFor={id}>Commentaire (facultatif)</label><textarea id={id} rows={2} value={comment} onChange={(e) => setComment(e.target.value)} /></div>
      {!ap.can_decide && <p className="hint">Vous ne pouvez pas approuver votre propre demande.</p>}
      {error && <p role="alert" className="field-error">Erreur : {error.message}</p>}
      <div className="ap-buttons">
        <button type="button" className="danger" disabled={!ap.can_decide || busy} onClick={() => decide(false)}>Refuser</button>
        <button type="button" className="primary" disabled={!ap.can_decide || busy} onClick={() => decide(true)}>Approuver</button>
      </div>
    </div>
  );
}

function ApprovalRow({ ap, slug, canApprove }: { ap: Approval; slug: string; canApprove: boolean }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState("request");
  const [comment, setComment] = useState("");
  const decide = useMutation({
    mutationFn: (approve: boolean) => api(`/workspaces/${slug}/approvals/${ap.id}/decide/`, { json: { approve, comment } }),
    onSuccess: () => { setOpen(false); void qc.invalidateQueries({ queryKey: ["approvals"] }); },
  });
  const pending = ap.status === "pending";
  return (
    <li className="approval-row">
      <div className="ar-main">
        <strong>{ap.title}</strong>
        <span className="hint">{ap.workflow} · demandé par {ap.requested_by ?? "—"} le {formatDate(ap.created_at)}</span>
        {!pending && <span className="hint">{ap.status === "approved" ? "✔ Approuvée" : "✖ Refusée"} par {ap.decided_by} le {formatDate(ap.decided_at)}{ap.comment ? ` — « ${ap.comment} »` : ""}</span>}
      </div>
      <button type="button" className={pending ? "primary" : undefined} onClick={() => { setTab("request"); setOpen(true); }} aria-label={`${pending ? "Examiner" : "Voir"} : ${ap.title}`}>{pending ? "Examiner" : "Voir"}</button>
      {open && (
        <Overlay title={ap.title} description={ap.subject || `Workflow : ${ap.workflow}`} variant="modal" wide className="overlay-approval" onClose={() => setOpen(false)}
          footer={<DecisionBar ap={ap} canApprove={canApprove} comment={comment} setComment={setComment} error={decide.error as ApiError | null} busy={decide.isPending} decide={(approve) => decide.mutate(approve)} />}>
          <Tabs label="Détail de la demande" active={tab} onChange={setTab} tabs={[
            { id: "request", label: "Demande", content: <RequestTab ap={ap} /> },
            { id: "context", label: "Contexte de la demande", content: <ContextTab ap={ap} /> },
          ]} />
        </Overlay>
      )}
    </li>
  );
}

interface Promo { id: number; workflow: string; workflow_slug: string; version: string; environment: string; status: string; requested_by: string | null; reviewed_by: string | null; comment: string; created_at: string; can_decide: boolean }

function PromotionRow({ p, canReview }: { p: Promo; canReview: boolean }) {
  const decide = useWsMutation<{ id: number; approve: boolean; comment: string }>("POST", (i) => `/promotions/${i.id}/decide/`, ["promotions"]);
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState("");
  const send = (approve: boolean) => decide.mutate({ id: p.id, approve, comment }, { onSuccess: () => setOpen(false) });
  return (
    <li className="approval-row">
      <div className="ar-main">
        <strong>{p.workflow} — version {p.version}</strong>
        <span className="hint">Vers {p.environment === "prod" ? "Production" : "Recette"} · demandée par {p.requested_by} le {formatDate(p.created_at)}</span>
      </div>
      <button type="button" className="primary" onClick={() => setOpen(true)} aria-label={`Examiner la promotion de ${p.workflow} ${p.version}`}>Examiner</button>
      {open && (
        <Overlay title={`Promotion de ${p.workflow} — version ${p.version}`} description={`Vers : ${p.environment === "prod" ? "Production" : "Recette"} — demandée par ${p.requested_by} le ${formatDate(p.created_at)}`} onClose={() => setOpen(false)}
          footer={canReview ? <>
            <button type="button" className="danger" disabled={!p.can_decide || decide.isPending} onClick={() => send(false)}>Refuser</button>
            <button type="button" className="primary" disabled={!p.can_decide || decide.isPending} onClick={() => send(true)}>Approuver la promotion</button></> : undefined}>
          {p.comment && <p>« {p.comment} »</p>}
          {canReview ? (
            <>
              <div className="field"><label htmlFor={`pc-${p.id}`}>Commentaire de revue</label><input id={`pc-${p.id}`} value={comment} onChange={(e) => setComment(e.target.value)} /></div>
              {!p.can_decide && <p className="hint">Séparation des rôles : vous ne pouvez pas réviser votre propre demande.</p>}
            </>
          ) : <p className="hint">Votre rôle ne permet pas de réviser.</p>}
          {decide.error && <p role="alert" className="field-error">Erreur : {errorText(decide.error)}</p>}
        </Overlay>
      )}
    </li>
  );
}

function Promotions({ canReview }: { canReview: boolean }) {
  const q = useWsQuery<{ results: Promo[] }>("promotions", "/promotions/", { refetchInterval: 5000 });
  const open = (q.data?.results ?? []).filter((p) => p.status === "requested");
  const paged = usePaged(open, 5);
  return (
    <section aria-labelledby="promos" className="card">
      <h2 id="promos">Promotions à revoir <HelpTip>Une version doit être promue (et revue par une autre personne) avant de s'exécuter en production.</HelpTip></h2>
      {open.length === 0 && <p>Aucune promotion en attente.</p>}
      <ul className="approval-list">{paged.slice.map((p) => <PromotionRow key={p.id} p={p} canReview={canReview} />)}</ul>
      {paged.pager("Pagination des promotions")}
    </section>
  );
}

export function ApprovalsPage() {
  const { workspace, can } = useApp();
  const [tab, setTab] = useState<"pending" | "done">("pending");
  const { data, isLoading } = useQuery({
    queryKey: ["approvals", workspace.slug, tab],
    queryFn: () => api<Page<Approval>>(`/workspaces/${workspace.slug}/approvals/${tab === "pending" ? "?status=pending" : ""}`),
    refetchInterval: 5000,
  });
  const list = (data?.results ?? []).filter((a) => (tab === "pending" ? a.status === "pending" : a.status !== "pending"));
  const paged = usePaged(list, 10);
  const content = (
    <>
      {isLoading && <p role="status">Chargement…</p>}
      {!isLoading && list.length === 0 && <p className="empty-state">{tab === "pending" ? "Aucune demande en attente. Tout est à jour." : "Aucune décision pour l'instant."}</p>}
      <ul className="approval-list card">{paged.slice.map((a) => <ApprovalRow key={a.id} ap={a} slug={workspace.slug} canApprove={can("approve")} />)}</ul>
      {paged.pager("Pagination des approbations")}
    </>
  );
  return (
    <>
      <PageHeader title="Approbations" description="Les décisions humaines demandées par les workflows. Ouvrez une demande pour voir son contexte, puis approuvez ou refusez." />
      <Tabs label="Filtre" active={tab} onChange={(t) => setTab(t as "pending" | "done")} tabs={[
        { id: "pending", label: "À traiter", content },
        { id: "done", label: "Historique", content },
      ]} />
      <Promotions canReview={can("approve")} />
    </>
  );
}
