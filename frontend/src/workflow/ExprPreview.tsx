import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "../api";
import { useApp } from "../context";
import { useDraft } from "./DraftContext";

interface Result { ok: boolean; value?: unknown; error?: string; has_data?: boolean }
const text = (v: unknown) => { const t = typeof v === "string" ? v : JSON.stringify(v); return t.length > 160 ? `${t.slice(0, 160)}…` : t; };

/** Live value of an expression on the data of the last run (and pinned outputs). `items` makes `item` available. */
export function ExprPreview({ expr, items }: { expr: string; items?: string }) {
  const { workspace } = useApp();
  const d = useDraft();
  const [debounced, setDebounced] = useState(expr);
  useEffect(() => { const h = setTimeout(() => setDebounced(expr), 500); return () => clearTimeout(h); }, [expr]);
  const pins = JSON.stringify(d.graph.pins ?? {});
  const q = useQuery({
    queryKey: ["eval", workspace.slug, d.slug, debounced, items ?? "", pins],
    enabled: Boolean(debounced.trim()),
    staleTime: 5000,
    retry: false,
    queryFn: () => api<Result>(`/workspaces/${workspace.slug}/workflows/${d.slug}/debug/eval/`, { json: { expr: debounced, items, graph: { variables: d.graph.variables, pins: d.graph.pins, nodes: [], edges: [] } } }),
  });
  if (!debounced.trim() || q.isLoading || q.isError || !q.data) return null;
  if (q.data.ok) return <p className="expr-preview" aria-live="polite">Aperçu : <code>{text(q.data.value)}</code></p>;
  return <p className="expr-preview bad" aria-live="polite">{q.data.has_data ? q.data.error : `${q.data.error} — lancez le workflow ou épinglez une sortie pour voir un aperçu.`}</p>;
}
