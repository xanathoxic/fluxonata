import { type ComponentType, type LazyExoticComponent, lazy } from "react";

/** A page loaded on demand from a module with named exports: `lazyPage(() => import("./pages/X"), "X")`. */
export function lazyPage<P extends object, K extends string>(load: () => Promise<Record<K, ComponentType<P>>>, name: K): LazyExoticComponent<ComponentType<P>> {
  return lazy(async () => ({ default: (await load())[name] }));
}
