import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useApp } from "../context";
import type { ExtensionHost, ExtensionInfo, ExtensionMount } from "./types";

/** Charge le module d'une extension (ES module de la même origine) ; remplaçable dans les tests. */
export type ModuleLoader = (url: string) => Promise<{ default?: unknown }>;
const defaultLoader: ModuleLoader = (url) => import(/* @vite-ignore */ url);

interface Props {
  extensions: ExtensionInfo[];
  me: { username: string };
  load?: ModuleLoader;
}

/** Page d'une extension : monte son module dans un cadre isolé de l'application, avec pour seul accès l'objet `host`. */
export function ExtensionPage({ extensions, me, load = defaultLoader }: Props) {
  const { ext = "", "*": rest = "" } = useParams();
  const { workspace } = useApp();
  const navigate = useNavigate();
  const box = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  const info = extensions.find((e) => e.id === ext);
  const path = `/${rest}`;
  const url = info?.ui ?? null;

  useEffect(() => {
    const el = box.current;
    if (!el || !url) return;
    let cancelled = false;
    let unmount: (() => void) | undefined;
    setError("");
    load(url)
      .then((mod) => {
        if (cancelled) return;
        if (typeof mod.default !== "function") throw new Error("pas de fonction par défaut");
        const host: ExtensionHost = {
          apiVersion: 1,
          workspace: { slug: workspace.slug, name: workspace.name },
          user: { username: me.username },
          path,
          api: (p, init) => api(p, init),
          navigate: (to) => navigate(to),
        };
        unmount = (mod.default as ExtensionMount)(el, host);
      })
      .catch(() => {
        if (!cancelled) setError("Cette extension n'a pas pu être chargée.");
      });
    return () => {
      cancelled = true;
      try {
        if (typeof unmount === "function") unmount();
      } finally {
        el.replaceChildren();
      }
    };
  }, [url, path, workspace.slug, workspace.name, me.username, navigate, load]);

  if (!info || !url) return <p role="alert">Extension introuvable, ou aucune page à afficher.</p>;
  return (
    <section aria-label={info.name}>
      {error && (
        <p role="alert" className="field-error">
          {error}
        </p>
      )}
      <div ref={box} data-extension={info.id} />
    </section>
  );
}
