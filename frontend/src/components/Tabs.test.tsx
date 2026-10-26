import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Tabs } from "./Tabs";

function Demo() {
  const [tab, setTab] = useState("a");
  return <Tabs label="Sections" active={tab} onChange={setTab} tabs={[{ id: "a", label: "Alpha", content: <p>Contenu A</p> }, { id: "b", label: "Beta", content: <p>Contenu B</p> }, { id: "c", label: "Gamma", content: <p>Contenu C</p> }]} />;
}

describe("Tabs", () => {
  it("exposes tablist/tab/tabpanel semantics and only renders the active panel", () => {
    render(<Demo />);
    expect(screen.getByRole("tablist", { name: "Sections" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Alpha" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Contenu A");
    expect(screen.queryByText("Contenu B")).not.toBeInTheDocument();
  });

  it("moves with the arrow keys (wrapping) and keeps a single tab stop", async () => {
    render(<Demo />);
    await userEvent.click(screen.getByRole("tab", { name: "Alpha" }));
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Beta" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Beta" })).toHaveFocus();
    await userEvent.keyboard("{ArrowLeft}{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "Gamma" })).toHaveAttribute("aria-selected", "true"); // wrapped around
    expect(screen.getByRole("tab", { name: "Alpha" })).toHaveAttribute("tabindex", "-1");
  });
});
