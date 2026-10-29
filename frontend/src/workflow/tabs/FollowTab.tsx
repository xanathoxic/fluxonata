import { useSearchParams } from "react-router-dom";
import { useDraft } from "../DraftContext";
import { ALL_ACTIVE, AllActiveFollow, RunPicker, SingleRunFollow, useWorkflowRuns } from "../RunFollow";

/** Live follow-up of the executions of this workflow, drawn on its diagram. The selected execution is in the URL (?run=), so several runs can be followed in several windows. */
export function FollowTab() {
  const { slug } = useDraft();
  const [params, setParams] = useSearchParams();
  const run = params.get("run");
  const runs = useWorkflowRuns(slug);
  const select = (id: string | null) => setParams(id ? { run: id } : {}, { replace: true });
  return (
    <section aria-label="Suivi des exécutions du workflow" className="follow-tab">
      <h2>Suivi d'exécution</h2>
      {runs.data && runs.data.results.length === 0 && <p className="hint">Ce workflow n'a pas encore été exécuté. Lancez-le : son avancement s'affichera ici, étape par étape.</p>}
      <RunPicker slug={slug} selected={run} onSelect={select} />
      {!run && (runs.data?.results.length ?? 0) > 0 && <p className="hint">Choisissez une exécution ci-dessus pour voir son avancement sur le schéma.</p>}
      {run === ALL_ACTIVE ? <AllActiveFollow slug={slug} /> : run ? <SingleRunFollow key={run} id={run} /> : null}
    </section>
  );
}
