import { Component, type ReactNode, Suspense } from "react";

/** Waits for a part of the application loaded on demand: an accessible loading message, and, if the download fails (network, new
 *  version published), a message with a way to reload instead of a blank page. */
class LoadFailure extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error("LazyBoundary", error);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div role="alert" className="error">
        <p>Impossible de charger cette partie de l'application (réseau coupé ou nouvelle version publiée).</p>
        <button type="button" onClick={() => location.reload()}>Recharger la page</button>
      </div>
    );
  }
}

export function LazyBoundary({ children, label = "Chargement…" }: { children: ReactNode; label?: string }) {
  return (
    <LoadFailure>
      <Suspense fallback={<p role="status" className="hint">{label}</p>}>{children}</Suspense>
    </LoadFailure>
  );
}
