import { formDiff } from "./formDiff";
import type { FormDefinition, FormField } from "./types";

const def = (...fields: Partial<FormField>[]): FormDefinition => ({ version: 1, sections: [{ id: "s", title: "S", fields: fields as FormField[] }] });
const text = (key: string, extra: Partial<FormField> = {}): Partial<FormField> => ({ key, label: key.toUpperCase(), type: "text", ...extra });

describe("formDiff", () => {
  it("a first publication adds everything and suggests an addition", () => {
    const d = formDiff(null, def(text("a"), text("b")));
    expect(d.added).toEqual(["A (a)", "B (b)"]);
    expect(d.suggested).toBe("minor");
  });
  it("a removed field or a changed type is a breaking change", () => {
    expect(formDiff(def(text("a"), text("b")), def(text("a"))).suggested).toBe("major");
    const t = formDiff(def(text("a")), def({ ...text("a"), type: "number" }));
    expect(t.typeChanged).toEqual(["A (a)"]);
    expect(t.suggested).toBe("major");
  });
  it("a modified label or rule is a correction; nothing changed is a correction too", () => {
    const d = formDiff(def(text("a")), def(text("a", { label: "Autre" })));
    expect(d.changed).toEqual(["Autre (a)"]);
    expect(d.suggested).toBe("patch");
    expect(formDiff(def(text("a")), def(text("a"))).changed).toEqual([]);
  });
  it("an added field alongside a correction is an addition", () => {
    expect(formDiff(def(text("a")), def(text("a", { label: "x" }), text("b"))).suggested).toBe("minor");
  });
});
