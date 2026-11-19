import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import type { ExtensionInfo, ExtensionsAnswer } from "./types";

/** Les extensions de l'espace. Une panne, ou aucune extension, donne une liste vide : l'application ne dépend jamais d'elles. */
export function useExtensions(workspaceSlug: string, enabled: boolean): ExtensionInfo[] {
  const q = useQuery({
    queryKey: ["extensions", workspaceSlug],
    queryFn: () => api<ExtensionsAnswer>(`/workspaces/${workspaceSlug}/extensions/`),
    enabled,
    retry: false,
    staleTime: 60_000,
  });
  return q.data?.extensions ?? [];
}
