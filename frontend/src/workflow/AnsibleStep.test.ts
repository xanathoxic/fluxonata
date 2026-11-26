import { describe, expect, it } from "vitest";
import { sampleOf } from "./AnsibleStep";
import { defaultNode, emptyGraph } from "./graphOps";

describe("valeurs d'essai d'une étape Playbook", () => {
  it("reprend les valeurs en clair, laisse de côté les expressions et force le mode vérification quand rien n'est dit", () => {
    const n = {
      ...defaultNode(emptyGraph(), "ansible"),
      inputs: { inventory_ref: "lab", known_hosts: "{{ inputs.hosts }}", user: "deploy", become: true, limit: "" },
    };
    expect(JSON.parse(sampleOf(n))).toEqual({ inventory_ref: "lab", user: "deploy", become: true, check: true });
  });
  it("respecte un mode vérification choisi à faux", () => {
    const n = { ...defaultNode(emptyGraph(), "ansible"), inputs: { inventory_ref: "lab", check: false } };
    expect(JSON.parse(sampleOf(n)).check).toBe(false);
  });
});
