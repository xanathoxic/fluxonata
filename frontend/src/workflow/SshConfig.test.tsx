import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { SshConfig, asJumps, fromJumps, move } from "./SshConfig";
import type { GraphNode } from "./types";

const conns = [
  { name: "cible", label: "Cible", kind: "ssh", config: { host: "10.0.0.9", host_key_sha256: "SHA256:x" } },
  { name: "bastion", label: "Bastion", kind: "ssh", config: { host: "10.0.0.1", host_key_sha256: "SHA256:y" } },
  { name: "relais", label: "Relais", kind: "ssh", config: { host: "10.0.0.2", host_key_sha256: "SHA256:z" } },
  { name: "idt", label: "Identité", kind: "ssh", config: {} }, // no host: a reusable key, the step names the host
  { name: "depot", label: "Dépôt SFTP", kind: "sftp", config: {} },
];

vi.mock("../lib", async (orig) => ({
  ...(await orig<typeof import("../lib")>()),
  useWsQuery: (key: string) => ({ data: key === "connections" ? { results: conns } : { results: [{ name: "mdp-equipement" }] } }),
}));
vi.mock("../context", () => ({ useApp: () => ({ can: () => true }) }));

function Harness({ start }: { start?: Partial<GraphNode> }) {
  const [node, setNode] = useState<GraphNode>({ id: "ssh", type: "ssh", name: "SSH", connection: "cible", jumps: [], mode: "test", session: "exec", commands: [{ run: "" }], ...start });
  return (
    <>
      <SshConfig node={node} onUpdate={(p) => setNode((n) => ({ ...n, ...p }))} />
      <output aria-label="nœud">{JSON.stringify(node)}</output>
    </>
  );
}
const state = (): GraphNode => JSON.parse(screen.getByLabelText("nœud").textContent ?? "{}") as GraphNode;

describe("SshConfig", () => {
  it("offers only SSH connections and never the target (or a jump already chosen) as a jump", async () => {
    render(<Harness start={{ jumps: ["bastion"] }} />);
    const target = screen.getByLabelText("Connexion cible (serveur SSH)");
    expect(within(target).queryByRole("option", { name: "Dépôt SFTP" })).toBeNull();
    const jump = screen.getByLabelText("Rebond 1");
    expect(within(jump).getAllByRole("option").map((o) => o.textContent)).toEqual(["Choisir…", "Bastion", "Relais", "Identité"]);
  });

  it("adds, reorders and removes jumps from the keyboard-reachable buttons, up to five", async () => {
    render(<Harness />);
    expect(screen.getByText("Connexion directe.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Ajouter un rebond" }));
    await userEvent.selectOptions(screen.getByLabelText("Rebond 1"), "bastion");
    await userEvent.click(screen.getByRole("button", { name: "Ajouter un rebond" }));
    await userEvent.selectOptions(screen.getByLabelText("Rebond 2"), "relais");
    expect(state().jumps).toEqual(["bastion", "relais"]);
    await userEvent.click(screen.getByRole("button", { name: "Descendre le rebond 1" }));
    expect(state().jumps).toEqual(["relais", "bastion"]);
    expect(screen.getByRole("button", { name: "Monter le rebond 1" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Retirer le rebond 2" }));
    expect(state().jumps).toEqual(["relais"]);
  });

  it("bounds the list of jumps", () => {
    render(<Harness start={{ jumps: ["a", "b", "c", "d", "e"] }} />);
    expect(screen.getByRole("button", { name: "Ajouter un rebond" })).toBeDisabled();
  });

  it("shows only what the chosen session needs", async () => {
    render(<Harness />);
    expect(screen.getByLabelText("Commande 1")).toBeInTheDocument();
    expect(screen.queryByText("Étapes de la session")).toBeNull();
    await userEvent.click(screen.getByLabelText("Session interactive (shell)"));
    expect(screen.queryByLabelText("Commande 1")).toBeNull();
    expect(screen.getByText("Étapes de la session")).toBeInTheDocument();
    expect(screen.queryByLabelText("Colonnes (20 à 500)")).toBeNull(); // PTY options only once a PTY is requested
    await userEvent.click(screen.getByLabelText("Demander un pseudo-terminal"));
    expect(screen.getByLabelText("Colonnes (20 à 500)")).toHaveValue(80);
    expect(state().pty).toMatchObject({ enabled: true });
  });

  it("edits commands, marks them sensitive, and can take the list from an expression", async () => {
    render(<Harness />);
    await userEvent.type(screen.getByLabelText("Commande 1"), "uptime");
    await userEvent.click(screen.getByLabelText("Sensible"));
    expect(state().commands).toEqual([{ run: "uptime", sensitive: true }]);
    await userEvent.click(screen.getByLabelText("Liste produite par une étape précédente (expression)"));
    expect(state().commands).toBe("");
    await userEvent.type(screen.getByLabelText("Expression de la liste"), "{{{{ steps.a.output.l }}");
    expect(state().commands).toBe("{{ steps.a.output.l }}");
  });

  it("references a secret by alias only: no value ever enters the node", async () => {
    render(<Harness start={{ session: "shell", steps: [{ expect: ["Password:"] }] }} />);
    const kind = screen.getByLabelText("Envoyer");
    expect(within(kind).getByRole("option", { name: /Un secret référencé/ })).toBeDisabled(); // no alias declared yet
    await userEvent.click(screen.getByRole("button", { name: "Ajouter un secret" }));
    await userEvent.type(screen.getByLabelText("Secret de l'alias 1"), "mdp-equipement");
    await userEvent.selectOptions(screen.getByLabelText("Envoyer"), "secret");
    const node = state();
    expect(node.secrets).toEqual({ secret1: "mdp-equipement" });
    expect(node.steps?.[0]).toMatchObject({ send_secret: "secret1" });
    expect(JSON.stringify(node)).not.toMatch(/"send":"[^"]+"/);
  });

  it("refuses to give two aliases the same name", async () => {
    render(<Harness start={{ secrets: { a: "s1", b: "s2" } }} />);
    const second = screen.getByLabelText("Alias 2");
    fireEvent.change(second, { target: { value: "a" } }); // renaming « b » to the existing « a »
    expect(state().secrets).toEqual({ a: "s1", b: "s2" });
  });

  it("validates the simulated results JSON and keeps the last valid value", async () => {
    render(<Harness />);
    const box = screen.getByLabelText("Résultats simulés (JSON, facultatif)");
    await userEvent.click(box);
    await userEvent.paste('{"pas": "une liste"}');
    expect(screen.getByRole("alert")).toHaveTextContent("JSON invalide");
    await userEvent.clear(box);
    await userEvent.click(box);
    await userEvent.paste('[{"id": "c1", "stdout": "ok"}]');
    expect(screen.queryByRole("alert")).toBeNull();
    expect(state().mocks).toEqual([{ id: "c1", stdout: "ok" }]);
  });

  it("hides the simulation field in real mode", async () => {
    render(<Harness start={{ mode: "reel" }} />);
    expect(screen.queryByLabelText("Résultats simulés (JSON, facultatif)")).toBeNull();
  });

  it("gives every control an accessible name", () => {
    render(<Harness start={{ session: "shell", steps: [{ expect: ["x"], responses: [{ expect: "y", send: "z" }] }], secrets: { a: "mdp-equipement" }, jumps: ["bastion"], pty: { enabled: true } }} />);
    const controls = [...screen.getAllByRole("textbox"), ...screen.getAllByRole("combobox"), ...screen.getAllByRole("checkbox"), ...screen.getAllByRole("spinbutton"), ...screen.getAllByRole("radio")];
    expect(controls.length).toBeGreaterThan(15);
    for (const c of controls) expect(c).toHaveAccessibleName();
  });
});

describe("SshConfig with what an assistant or an import may have written", () => {
  it("reads patterns written as one text, commands written as plain texts, and ignores what it cannot read", () => {
    const odd = { session: "shell", steps: [{ id: "a", send: "show version\n", expect: "#\\s*$", timeout_seconds: 10 }, null, 7], jumps: ["a", 3], commands: ["uptime", { run: "id" }, 5] } as unknown as Partial<GraphNode>;
    render(<Harness start={odd} />);
    expect(screen.getByLabelText("Attendre (un motif par ligne)")).toHaveValue("#\\s*$");
    expect(screen.getAllByText(/^Étape \d/)).toHaveLength(1);
  });

  it("shows plain-text commands as commands in the exec session", () => {
    render(<Harness start={{ commands: ["uptime", { run: "id" }] as unknown as GraphNode["commands"] }} />);
    expect(screen.getByLabelText("Commande 1")).toHaveValue("uptime");
    expect(screen.getByLabelText("Commande 2")).toHaveValue("id");
  });
});

describe("SshConfig: the host chosen in the step", () => {
  const FP = `SHA256:${"A".repeat(43)}`;

  it("asks for the host, port and fingerprint only when the connection has no host of its own", async () => {
    render(<Harness start={{ connection: "cible" }} />);
    expect(screen.queryByLabelText("Hôte : adresse IP ou nom DNS")).toBeNull();
    expect(screen.getByText(/Hôte défini par la connexion « Cible »/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Connexion cible (serveur SSH)"), "idt");
    await userEvent.type(screen.getByLabelText("Hôte : adresse IP ou nom DNS"), "srv1.exemple.org");
    await userEvent.type(screen.getByLabelText("Port (22 par défaut)"), "2222");
    await userEvent.type(screen.getByLabelText("Empreinte de la clé du serveur (SHA256:…)"), FP);
    expect(state()).toMatchObject({ connection: "idt", host: "srv1.exemple.org", port: "2222", host_key_sha256: FP });
  });

  it("keeps a jump a plain name when its host comes from the connection, and an object when the step names the host", async () => {
    render(<Harness start={{ connection: "idt", host: "cible.exemple.org", host_key_sha256: FP }} />);
    await userEvent.click(screen.getByRole("button", { name: "Ajouter un rebond" }));
    await userEvent.selectOptions(screen.getByLabelText("Rebond 1"), "bastion");
    expect(state().jumps).toEqual(["bastion"]);
    await userEvent.selectOptions(screen.getByLabelText("Rebond 1"), "idt");
    await userEvent.type(screen.getByLabelText("Hôte : adresse IP ou nom DNS", { selector: "#ssh-jump-0-host" }), "bastion.exemple.org");
    await userEvent.type(screen.getByLabelText("Empreinte de la clé du serveur (SHA256:…)", { selector: "#ssh-jump-0-fp" }), FP);
    expect(state().jumps).toEqual([{ connection: "idt", host: "bastion.exemple.org", host_key_sha256: FP }]);
  });

  it("lets one identity serve the target and a jump, but not a connection that has its own host", async () => {
    render(<Harness start={{ connection: "idt", jumps: [{ connection: "idt", host: "b.exemple.org", host_key_sha256: FP }, "bastion"] }} />);
    const second = within(screen.getByLabelText("Rebond 2")).getAllByRole("option").map((o) => o.textContent);
    expect(second).toContain("Identité"); // reusable
    expect(within(screen.getByLabelText("Rebond 1")).queryByRole("option", { name: "Bastion" })).toBeNull(); // already used elsewhere, with its own host
  });
});

describe("jumps as stored", () => {
  it("round-trips names and objects, and reads what an assistant may have written", () => {
    expect(asJumps(["a", { connection: "b", host: "h" }, 3, null])).toEqual([{ connection: "a" }, { connection: "b", host: "h" }]);
    expect(fromJumps([{ connection: "a" }, { connection: "b", host: "h" }])).toEqual(["a", { connection: "b", host: "h" }]);
  });
});

describe("move", () => {
  it("swaps neighbours and leaves the ends alone", () => {
    expect(move([1, 2, 3], 0, 1)).toEqual([2, 1, 3]);
    expect(move([1, 2, 3], 0, -1)).toEqual([1, 2, 3]);
    expect(move([1, 2, 3], 2, 1)).toEqual([1, 2, 3]);
  });
});
