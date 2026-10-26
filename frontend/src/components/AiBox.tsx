import { useWsQuery } from "../lib";

export interface AiPolicyInfo { mode: string; used_today: number; daily_token_budget: number }

/** Returns the workspace AI mode ("off" by default) so assistant buttons only appear when an admin enabled them. */
export function useAiMode(): { enabled: boolean; mode: string } {
  const q = useWsQuery<AiPolicyInfo>("aipolicy-view", "/ai/policy/");
  const mode = q.data?.mode ?? "off";
  return { enabled: mode !== "off", mode };
}

export function AiNotice({ mode }: { mode: string }) {
  return <p className="hint">{mode === "local_strict" ? "Assistant IA en mode local strict : aucune donnée ne quitte votre infrastructure." : "Assistant IA : les données peuvent être envoyées à un fournisseur cloud choisi par votre administrateur (e-mails, IP et jetons masqués)."} Les suggestions sont indicatives et doivent être relues.</p>;
}
