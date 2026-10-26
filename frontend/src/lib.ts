import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ApiError } from "./api";
import { useApp } from "./context";

export function useWsQuery<T>(key: string, path: string, opts: { enabled?: boolean; refetchInterval?: number } = {}) {
  const { workspace } = useApp();
  return useQuery({ queryKey: [key, workspace.slug], queryFn: () => api<T>(`/workspaces/${workspace.slug}${path}`), ...opts });
}

/** POST/PUT/DELETE against the current workspace; invalidates the given query keys on success. */
export function useWsMutation<I = unknown, O = unknown>(method: "POST" | "PUT" | "PATCH" | "DELETE", path: string | ((i: I) => string), invalidate: string[] = []) {
  const { workspace } = useApp();
  const qc = useQueryClient();
  return useMutation<O, ApiError, I>({
    mutationFn: (input) => api<O>(`/workspaces/${workspace.slug}${typeof path === "function" ? path(input) : path}`, { method, json: method === "DELETE" ? undefined : (input ?? {}) }),
    onSuccess: () => { invalidate.forEach((k) => void qc.invalidateQueries({ queryKey: [k] })); },
  });
}

export function errorText(e: unknown): string {
  const err = e as Partial<ApiError> | null;
  if (!err) return "";
  const d = err.details as Record<string, unknown> | null;
  if (d && typeof d === "object") {
    const parts = Object.entries(d).filter(([k]) => k !== "issues").map(([k, v]) => `${k} : ${Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : String(v)}`);
    if (parts.length) return parts.join(" — ");
  }
  return err.message ?? "Erreur";
}
