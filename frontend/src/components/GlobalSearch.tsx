import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, apiAll, type Action, type Page, type WorkflowRun, type WorkflowSummary } from "../api";
import { useApp } from "../context";
import { formatDate } from "../i18n";
import { Overlay } from "./Overlay";

interface Hit { group: string; title: string; sub?: string; to: string }
interface Named { slug: string; name: string }

/** Ctrl+K: one box to jump to a workflow, an action, a form, a control, a connection or a recent run. The lists are read when the box opens
 *  and filtered here; what a person may not see is simply not returned by the server. */
export function GlobalSearch({ onClose }: { onClose: () => void }) {
  const { workspace, can } = useApp();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const ws = `/workspaces/${workspace.slug}`;
  const lists = useQuery({
    queryKey: ["global-search", workspace.slug],
    staleTime: 30_000,
    queryFn: async (): Promise<Hit[]> => {
      const safe = <T,>(p: Promise<T>): Promise<T | null> => p.catch(() => null);
      const [wfs, acts, forms, ctls, conns, runs] = await Promise.all([
        safe(apiAll<WorkflowSummary>(`${ws}/workflows/`)), safe(apiAll<Action>(`${ws}/actions/?q=`)), safe(apiAll<Named>(`${ws}/form-library/`)),
        safe(api<{ results: Named[] }>(`${ws}/control-library/`)), safe(api<{ results: { name: string; kind: string }[] }>(`${ws}/connections/`)),
        safe(api<Page<WorkflowRun>>(`${ws}/workflow-runs/?limit=30`)),
      ]);
      return [
        ...(wfs?.results ?? []).map((w): Hit => ({ group: "Workflows", title: w.name, sub: w.slug, to: `/workflows/${w.slug}` })),
        ...(acts?.results ?? []).map((a): Hit => ({ group: "Actions", title: a.name, sub: a.slug, to: `/library/actions/${a.slug}` })),
        ...(forms?.results ?? []).map((f): Hit => ({ group: "Formulaires", title: f.name, sub: f.slug, to: `/library/forms/${f.slug}` })),
        ...(ctls?.results ?? []).map((c): Hit => ({ group: "Contrôles", title: c.name, sub: c.slug, to: `/library/controls/${c.slug}` })),
        ...(conns?.results ?? []).map((c): Hit => ({ group: "Connexions", title: c.name, sub: c.kind, to: "/connections" })),
        ...(runs?.results ?? []).map((r): Hit => ({ group: "Exécutions", title: r.workflow.name, sub: `${formatDate(r.created_at)} · ${r.status_label}`, to: `/runs/${r.id}` })),
      ];
    },
    enabled: can("view"),
  });
  const hits = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return [];
    const found = (lists.data ?? []).filter((h) => `${h.title} ${h.sub ?? ""}`.toLowerCase().includes(needle));
    const groups = new Map<string, Hit[]>();
    for (const h of found) { const g = groups.get(h.group) ?? []; if (g.length < 6) g.push(h); groups.set(h.group, g); }
    return [...groups.entries()];
  }, [q, lists.data]);
  const go = (to: string) => { onClose(); navigate(to); };
  const first = hits[0]?.[1][0];
  return (
    <Overlay variant="modal" title="Recherche" description="Workflows, actions, formulaires, contrôles, connexions et exécutions récentes." onClose={onClose}>
      <form role="search" onSubmit={(e) => { e.preventDefault(); if (first) go(first.to); }}>
        <label htmlFor="gs-q" className="sr-only">Rechercher dans l'espace</label>
        <input id="gs-q" className="gs-input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tapez un nom, un identifiant…" autoComplete="off" autoFocus />
      </form>
      <div role="status" aria-live="polite">
        {q.trim() === "" ? <p className="hint">Entrée ouvre le premier résultat.</p> : lists.isLoading ? <p className="hint">Recherche…</p> : hits.length === 0 ? <p className="hint">Aucun résultat pour « {q.trim()} ».</p> : null}
      </div>
      {hits.map(([group, items]) => (
        <section key={group} className="gs-group" aria-label={group}>
          <h3>{group}</h3>
          <ul className="gs-list">{items.map((h) => <li key={`${h.to}${h.title}${h.sub}`}><button type="button" onClick={() => go(h.to)}>{h.title}{h.sub && <span className="gs-sub">{h.sub}</span>}</button></li>)}</ul>
        </section>
      ))}
    </Overlay>
  );
}
