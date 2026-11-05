import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { PageTabs } from "./PageTabs";
import { Tabs } from "./Tabs";

const TABS = [{ id: "a", label: "Alpha" }, { id: "b", label: "Beta", badge: 3 }, { id: "c", label: "Gamma", badgeTone: "alert" as const, badge: "!" }];
function Bar() {
  const [tab, setTab] = useState("a");
  return <PageTabs label="Sections" active={tab} onChange={setTab} tabs={TABS} />;
}

describe("PageTabs", () => {
  it("is a tablist with one tab stop, the active tab selected, and badges in the label", () => {
    render(<Bar />);
    expect(screen.getByRole("tablist", { name: "Sections" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Alpha" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Alpha" })).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("tab", { name: /Beta/ })).toHaveAttribute("tabindex", "-1");
    expect(screen.getByRole("tab", { name: /Beta/ })).toHaveTextContent("3");
  });

  it("moves with the arrow keys (wrapping), Home and End", async () => {
    render(<Bar />);
    await userEvent.click(screen.getByRole("tab", { name: "Alpha" }));
    await userEvent.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: /Gamma/ })).toHaveFocus();
    expect(screen.getByRole("tab", { name: /Gamma/ })).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "Alpha" })).toHaveFocus();
    await userEvent.keyboard("{End}");
    expect(screen.getByRole("tab", { name: /Gamma/ })).toHaveFocus();
  });

  it("drives by the route when every tab has a link: a navigation with aria-current on the active one", () => {
    render(<MemoryRouter><PageTabs label="Sections du workflow" active="b" tabs={TABS.map((t) => ({ ...t, to: `/x/${t.id}` }))} /></MemoryRouter>);
    const nav = screen.getByRole("navigation", { name: "Sections du workflow" });
    expect(nav).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Beta/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Alpha" })).toHaveAttribute("href", "/x/a");
    expect(screen.getByRole("link", { name: "Alpha" })).not.toHaveAttribute("aria-current");
  });

  it("Tabs keeps the panel association on top of it", () => {
    render(<Tabs label="S" active="a" onChange={() => undefined} tabs={[{ id: "a", label: "A", content: <p>Contenu A</p> }, { id: "b", label: "B", content: <p>Contenu B</p> }]} />);
    const tab = screen.getByRole("tab", { name: "A" });
    expect(screen.getByRole("tabpanel")).toHaveAttribute("id", tab.getAttribute("aria-controls"));
    expect(screen.queryByText("Contenu B")).not.toBeInTheDocument();
  });
});
