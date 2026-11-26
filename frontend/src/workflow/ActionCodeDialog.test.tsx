import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ActionCodeDialog } from "./ActionCodeDialog";
import type { GraphNode } from "./types";

const versions = [
  {
    version: "2.0.0",
    runtime: "python",
    source: "print('deux')",
    source_digest: "sha256:aaaaaaaaaaaaaaaa",
    side_effects: "none",
    idempotent: true,
    network_policy: "none",
  },
  {
    version: "1.0.0",
    runtime: "python",
    source: "print('un')",
    source_digest: "sha256:bbbbbbbbbbbbbbbb",
    side_effects: "external",
    idempotent: false,
    network_policy: "allowlist",
  },
];
vi.mock("../lib", () => ({ useWsQuery: () => ({ data: { results: versions }, isLoading: false, isError: false }) }));
vi.mock("../components/SourceViewer", () => ({
  SourceViewer: ({ source, version }: { source: string; version?: string }) => <section aria-label={`code ${version}`}>{source}</section>,
}));

const node = { id: "a", type: "action", name: "Appel", action: "ping", version: "1.0.0" } as GraphNode;
const action = { slug: "ping", name: "Ping", description: "Teste une adresse", status: "active" } as never;

describe("ActionCodeDialog", () => {
  it("affiche la version appelée par l'étape, en lecture seule, et permet d'en consulter une autre", async () => {
    render(<ActionCodeDialog node={node} action={action} onClose={() => undefined} />);
    expect(screen.getByRole("dialog", { name: "Ping" })).toBeInTheDocument();
    expect(screen.getByLabelText("code 1.0.0")).toHaveTextContent("print('un')");
    expect(screen.getByText("Effet externe non idempotent")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Version"), "2.0.0");
    expect(screen.getByLabelText("code 2.0.0")).toHaveTextContent("print('deux')");
  });

  it("prend la dernière version quand l'étape n'en fige aucune, et se ferme", async () => {
    const onClose = vi.fn();
    render(<ActionCodeDialog node={{ ...node, version: undefined }} action={action} onClose={onClose} />);
    expect(screen.getByLabelText("code 2.0.0")).toBeInTheDocument();
    const [close] = screen.getAllByRole("button", { name: "Fermer" });
    await userEvent.click(close as HTMLElement);
    expect(onClose).toHaveBeenCalled();
  });
});
