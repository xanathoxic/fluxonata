import { type ComponentPropsWithoutRef, forwardRef, lazy } from "react";
import type { CodeEditor, CodeEditorHandle } from "./CodeEditor";
import { LazyBoundary } from "./LazyBoundary";

const Loaded = lazy(async () => ({ default: (await import("./CodeEditor")).CodeEditor }));

/** The code editor, downloaded when a page first shows one (its libraries weigh several hundred kB and most pages never need them). */
export const LazyCodeEditor = forwardRef<CodeEditorHandle, ComponentPropsWithoutRef<typeof CodeEditor>>(function LazyCodeEditor(props, ref) {
  return (
    <LazyBoundary label="Chargement de l'éditeur…">
      <Loaded ref={ref} {...props} />
    </LazyBoundary>
  );
});
