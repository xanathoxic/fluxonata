import { describe, expect, it } from "vitest";
import { clientValidate } from "./graphOps";
import type { Graph, GraphNode } from "./types";

const graph = (n: Partial<GraphNode>): Graph => ({
  inputs: { type: "object", properties: {}, required: [] },
  nodes: [{ id: "c", type: "smtp", name: "Courriel", connection: "x", to: "a@b.fr", subject: "Objet", ...n }],
  edges: [],
  outputs: {},
});
const smtpIssues = (n: Partial<GraphNode>) => clientValidate(graph(n), []).filter((i) => i.code === "bad_smtp");

describe("E-mail : contrôle côté éditeur", () => {
  it("exige un corps de message", () => {
    expect(smtpIssues({ body: "" })).toHaveLength(1);
  });

  it("accepte un modèle de document à la place du corps", () => {
    expect(smtpIssues({ body: "", body_model: "{{ steps.modele.output }}" })).toHaveLength(0);
    expect(smtpIssues({ body: "Bonjour" })).toHaveLength(0);
  });

  it("exige toujours le destinataire et l'objet", () => {
    expect(smtpIssues({ body_model: "{{ steps.m.output }}", to: "" })).toHaveLength(1);
    expect(smtpIssues({ body_model: "{{ steps.m.output }}", subject: "" })).toHaveLength(1);
  });
});
