import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { ShapingFields } from "./ServiceFields";
import type { DataSource } from "./types";

function Harness({ start }: { start: DataSource }) {
  const [value, setValue] = useState<DataSource>(start);
  return (
    <>
      <ShapingFields idPrefix="t" value={value} onChange={setValue} />
      <output aria-label="source">{JSON.stringify(value)}</output>
    </>
  );
}
const base: DataSource = { connection: "svc", path: "/acl", items_path: "groups" };
const shown = () => JSON.parse(screen.getByLabelText("source").textContent ?? "{}") as DataSource;

describe("ShapingFields", () => {
  it("écrit la forme, le tri, le filtre et le libellé calculé dans la source", async () => {
    const u = userEvent.setup();
    render(<Harness start={base} />);
    await u.selectOptions(screen.getByLabelText("Forme"), "entries");
    await u.selectOptions(screen.getByLabelText("Tri"), "label");
    await u.type(screen.getByLabelText("Garder si"), "item.actif");
    await u.type(screen.getByLabelText("Libellé calculé"), "item.nom");
    expect(shown()).toEqual({ ...base, items_as: "entries", sort: "label", where: "item.actif", label_expr: "item.nom" });
  });

  it("retire la clé quand on revient à la valeur par défaut ou qu'on vide le champ", async () => {
    const u = userEvent.setup();
    render(<Harness start={{ ...base, items_as: "entries", sort: "label_desc", where: "x" }} />);
    await u.selectOptions(screen.getByLabelText("Forme"), "list");
    await u.selectOptions(screen.getByLabelText("Tri"), "");
    await u.clear(screen.getByLabelText("Garder si"));
    expect(shown()).toEqual(base);
  });
});
