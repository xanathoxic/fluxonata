import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DiffView } from "./DiffView";

describe("DiffView", () => {
  it("numérote les lignes et distingue ajouts et suppressions", () => {
    render(<DiffView from="1.0.0" to="1.1.0" diff={"--- 1.0.0\n+++ 1.1.0\n@@ -1,2 +1,2 @@\n def run():\n-    return 1\n+    return 2\n"} />);
    expect(screen.getByRole("region", { name: /entre la version 1.0.0 et la version 1.1.0/ })).toBeInTheDocument();
    expect(screen.getByText(/Suppression/).closest("tr")).toHaveClass("df-del");
    expect(screen.getByText(/Ajout/).closest("tr")).toHaveClass("df-add");
  });
});
