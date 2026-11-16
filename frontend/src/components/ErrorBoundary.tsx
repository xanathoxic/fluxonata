import { Component, type ReactNode } from "react";

/** Keeps an unexpected shape of one piece of configuration from blanking the whole page: the rest of the editor stays usable. */
export class ErrorBoundary extends Component<{ children: ReactNode; label: string }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.error("ErrorBoundary", this.props.label, error);
  }

  render() {
    if (this.state.failed) {
      return <p role="alert" className="error">Impossible d'afficher « {this.props.label} » : sa configuration a une forme inattendue. Le reste de l'éditeur reste utilisable ; corrigez ou recréez cette étape.</p>;
    }
    return this.props.children;
  }
}
