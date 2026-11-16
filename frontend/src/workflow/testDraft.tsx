import type { ReactNode } from "react";
import type { WorkflowDetail } from "../api";
import { ReadOnlyDraftProvider } from "./DraftContext";
import type { Graph } from "./types";

/** Test helper: a parent draft context (the one an overlay opens from). */
export function DraftProviderForTest({ children }: { children: ReactNode }) {
  const wf = { name: "Parent", draft_graph: { inputs: { type: "object", properties: {} }, nodes: [], edges: [] } as unknown as Graph } as unknown as WorkflowDetail;
  return <ReadOnlyDraftProvider slug="parent" wf={wf} actions={[]} workflows={[]}>{children}</ReadOnlyDraftProvider>;
}
