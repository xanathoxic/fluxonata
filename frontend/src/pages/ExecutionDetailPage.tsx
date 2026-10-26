import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, type Execution } from "../api";
import { Fields } from "../components/Fields";
import { StatusBadge } from "../components/StatusBadge";
import { useApp } from "../context";
import { formatDate, t } from "../i18n";

export function ExecutionDetailPage() {
  const { id = "" } = useParams();
  const { workspace } = useApp();
  const { data: ex, isLoading, isError } = useQuery({
    queryKey: ["execution", workspace.slug, id],
    queryFn: () => api<Execution>(`/workspaces/${workspace.slug}/executions/${id}/`),
    refetchInterval: (q) => (q.state.data && ["queued", "running"].includes(q.state.data.status) ? 1500 : false),
  });
  const { data: action } = useQuery({
    queryKey: ["action", workspace.slug, ex?.action.slug],
    enabled: !!ex,
    queryFn: () => api<import("../api").Action>(`/workspaces/${workspace.slug}/actions/${ex?.action.slug}/`),
  });

  if (isLoading) return <p role="status">{t("common.loading")}</p>;
  if (isError || !ex) return <p role="alert">{t("common.error")}</p>;
  const active = ex.status === "queued" || ex.status === "running";
  const inputSchema = action?.latest_version?.input_schema ?? {};
  const logs = ex.attempts?.map((a) => a.logs).filter(Boolean).join("\n") ?? "";

  return (
    <>
      <nav aria-label={t("breadcrumb")} className="breadcrumb">
        <Link to="/executions">{t("nav.executions")}</Link> / <span aria-current="page">{ex.action.name}</span>
      </nav>
      <h1>{ex.action.name} <small>v{ex.action.version}</small></h1>
      <p>
        {t("exec.status")} : <StatusBadge status={ex.status} label={ex.status_label} />
        {ex.runner_name && <> · Exécutée par le runner <strong>{ex.runner_name}</strong></>}
      </p>
      <div role="status" aria-live="polite">
        {ex.status === "queued" && <p>{t("exec.queued")}</p>}
        {ex.status === "running" && <p>{t("exec.inProgress")}</p>}
        {ex.status === "succeeded" && <p>{t("exec.succeeded")}</p>}
        {ex.status === "failed" && <p>{t("exec.failed")}</p>}
      </div>

      {ex.status === "succeeded" && ex.result && (
        <section aria-labelledby="res">
          <h2 id="res">{t("exec.result")}</h2>
          <Fields schema={ex.action.output_schema} data={ex.result} />
        </section>
      )}
      {ex.status === "failed" && ex.error && (
        <section aria-labelledby="err" className="error-box">
          <h2 id="err">{t("exec.error")}</h2>
          <p>{ex.error.message}</p>
          {ex.error.next_step && (<><h3>{t("exec.nextStep")}</h3><p>{ex.error.next_step}</p></>)}
        </section>
      )}
      <section aria-labelledby="inp">
        <h2 id="inp">{t("action.inputs")}</h2>
        <Fields schema={inputSchema} data={ex.inputs} />
        {!active && (
          <Link to={`/library/actions/${ex.action.slug}/run`} state={{ inputs: ex.inputs }} className="button">{t("exec.rerun")}</Link>
        )}
      </section>
      <section aria-labelledby="tl">
        <h2 id="tl">{t("exec.timeline")}</h2>
        <ol className="timeline">
          {ex.events?.map((e) => (
            <li key={e.seq}><time dateTime={e.created_at}>{formatDate(e.created_at)}</time> — {e.message}</li>
          ))}
        </ol>
      </section>
      {logs && (
        <details>
          <summary>{t("exec.logs")}</summary>
          <pre tabIndex={0}>{logs}</pre>
        </details>
      )}
    </>
  );
}
