import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { useApp } from "../context";

export interface Fav { kind: string; slug: string; name: string }

export function useFavorites() {
  const { workspace } = useApp();
  return useQuery({ queryKey: ["favorites", workspace.slug], queryFn: () => api<{ results: Fav[] }>(`/workspaces/${workspace.slug}/favorites/`) });
}

/** ★/☆ toggle. The state is also written in words for assistive technologies (aria-pressed + label). */
export function FavoriteStar({ kind, slug, name }: { kind: "workflow" | "action"; slug: string; name: string }) {
  const { workspace } = useApp();
  const qc = useQueryClient();
  const { data } = useFavorites();
  const on = data?.results.some((f) => f.kind === kind && f.slug === slug) ?? false;
  const toggle = async () => {
    await api(`/workspaces/${workspace.slug}/favorites/${kind}/${slug}/`, { method: on ? "DELETE" : "PUT" });
    await qc.invalidateQueries({ queryKey: ["favorites"] });
  };
  return (
    <button type="button" className="star" aria-pressed={on} aria-label={on ? `Retirer ${name} des favoris` : `Ajouter ${name} aux favoris`} onClick={() => void toggle()}>
      <span aria-hidden="true">{on ? "★" : "☆"}</span>
    </button>
  );
}
