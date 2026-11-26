import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { StepRun } from "../api";
import { AnsibleTab, ansibleResult, hasAnsibleLog } from "./AnsibleLog";

const step = (over: Partial<StepRun> = {}): StepRun => ({
  node_id: "essai",
  name: "Essai à blanc",
  node_type: "action",
  status: "succeeded",
  status_label: "Réussie",
  attempt: 1,
  output: null,
  error: null,
  available_at: null,
  started_at: "2026-11-25T10:00:00Z",
  finished_at: null,
  approval_id: null,
  execution_id: null,
  child_run_ids: [],
  ...over,
});
const RESULT = {
  ok: true,
  check: true,
  hosts: 1,
  stats: { "serveur-test": { ok: 3, changed: 1, failures: 0, unreachable: 0, skipped: 1, rescued: 0, ignored: 0 } },
  tasks: [
    { host: "serveur-test", task: "Qui suis-je ?", status: "skipped", changed: false, msg: "Command would have run if not in check mode" },
    { host: "serveur-test", task: "Poser le fichier témoin", status: "ok", changed: true, msg: "" },
    { host: "serveur-test", task: "Annoncer", status: "failed", changed: false, msg: "boom", ignored: true },
  ],
  data: { fichier: "/tmp/x.txt" },
};

describe("Détection d'une étape Ansible", () => {
  it("reconnaît un résultat Ansible (bilan par machine et tâches)", () => {
    expect(ansibleResult(step({ output: RESULT }))?.hosts).toBe(1);
    expect(hasAnsibleLog(step({ output: RESULT }))).toBe(true);
  });
  it("reconnaît une étape en échec sur une erreur Ansible : le journal dit pourquoi", () => {
    expect(hasAnsibleLog(step({ status: "failed", error: { code: "ANSIBLE_FAILED", message: "1 échec(s)" } }))).toBe(true);
    expect(hasAnsibleLog(step({ status: "failed", error: { code: "INVENTORY_NOT_FOUND", message: "x" } }))).toBe(false);
  });
  it.each([[null], [{}], [{ stats: {}, tasks: [] }], [{ stats: [], tasks: [], hosts: 1 }], [{ stats: {}, tasks: "x", hosts: 1 }], [{ value: "texte" }]])(
    "ne confond pas avec une autre sortie : %j",
    (output) => {
      expect(hasAnsibleLog(step({ output: output as StepRun["output"] }))).toBe(false);
    },
  );
});

describe("Onglet Ansible", () => {
  it("montre le bilan par machine, les tâches avec leur résultat, les valeurs publiées et le journal", () => {
    render(<AnsibleTab steps={[step({ output: RESULT, logs: "PLAY [Etat] ****\nTASK [Qui] ****" })]} />);
    expect(screen.getByText("Vérification : rien n'a été modifié")).toBeInTheDocument();
    expect(screen.getByText("Réussi")).toBeInTheDocument();
    const hosts = within(screen.getByRole("table", { name: "Bilan par machine" }));
    expect(hosts.getByRole("rowheader", { name: "serveur-test" })).toBeInTheDocument();
    expect(hosts.getByRole("columnheader", { name: "Changerait" })).toBeInTheDocument(); // en vérification : « changerait », pas « modifiées »
    const tasks = within(screen.getByRole("table", { name: "Tâches jouées" }));
    expect(tasks.getByText("Ignorée")).toBeInTheDocument();
    expect(tasks.getByText("Modifiée")).toBeInTheDocument();
    expect(tasks.getByText("Échec")).toBeInTheDocument();
    expect(tasks.getByText("(erreur ignorée)")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Valeurs publiées par Essai à blanc" })).toHaveTextContent('"fichier": "/tmp/x.txt"');
    expect(screen.getByRole("region", { name: "Journal d'Ansible de Essai à blanc" })).toHaveTextContent("PLAY [Etat]");
  });
  it("hors vérification, la colonne dit « Modifiées » et un échec est signalé", () => {
    const bad = { ...RESULT, ok: false, check: false, stats: { m: { ok: 1, changed: 0, failures: 2, unreachable: 1, skipped: 0, rescued: 0, ignored: 0 } } };
    render(<AnsibleTab steps={[step({ output: bad })]} />);
    expect(screen.getByText("En échec")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Modifiées" })).toBeInTheDocument();
    expect(screen.queryByText(/Vérification/)).toBeNull();
  });
  it("une étape en échec montre l'erreur et le journal, sans tableaux", () => {
    render(
      <AnsibleTab
        steps={[
          step({
            status: "failed",
            output: null,
            error: { code: "ANSIBLE_FAILED", message: "1 échec(s), 0 hôte(s) injoignable(s)." },
            logs: "fatal: [h]: FAILED",
          }),
        ]}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("1 échec(s)");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("region", { name: "Journal d'Ansible de Essai à blanc" })).toHaveTextContent("fatal");
  });
  it("ignore les étapes qui ne sont pas Ansible et dit quand il n'y a pas de journal", () => {
    render(<AnsibleTab steps={[step({ node_id: "autre", name: "Autre", output: { v: 1 } }), step({ output: RESULT })]} />);
    expect(screen.queryByText("Autre")).toBeNull();
    expect(screen.getByText("Aucun journal enregistré pour cette étape.")).toBeInTheDocument();
  });
  it("signale les tâches non listées au-delà de la limite", () => {
    render(<AnsibleTab steps={[step({ output: { ...RESULT, tasks_dropped: 12 } })]} />);
    expect(screen.getByText(/12 tâche\(s\) de plus/)).toBeInTheDocument();
  });
});
