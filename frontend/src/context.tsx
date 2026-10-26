import { createContext, useContext } from "react";
import type { Me, Workspace } from "./api";

export interface Ctx {
  me: Me;
  workspace: Workspace;
  environment: string;
  setWorkspace: (slug: string) => void;
  setEnvironment: (kind: string) => void;
  can: (perm: string) => boolean;
}

export const AppContext = createContext<Ctx | null>(null);

export function useApp(): Ctx {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("AppContext missing");
  return ctx;
}
