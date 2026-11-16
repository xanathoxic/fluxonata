import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { LazyBoundary } from "./LazyBoundary";
import { lazyPage } from "./lazyPage";

const Demo = () => <p>contenu chargé</p>;
const SlowPage = () => <p>page lente</p>;
const Hello = ({ name }: { name: string }) => <p>bonjour {name}</p>;

describe("Chargement à la demande", () => {
  it("shows an accessible loading message, then the page", async () => {
    const Page = lazyPage(async () => ({ Demo }), "Demo");
    render(
      <LazyBoundary>
        <Page />
      </LazyBoundary>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Chargement…");
    expect(await screen.findByText("contenu chargé")).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("passes the props of the page through", async () => {
    const Page = lazyPage(async () => ({ Hello }), "Hello");
    render(
      <LazyBoundary>
        <Page name="Ana" />
      </LazyBoundary>,
    );
    expect(await screen.findByText("bonjour Ana")).toBeInTheDocument();
  });

  it("tells the user, with a way to reload, when the download fails", async () => {
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    const quiet = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const Page = lazyPage(async () => {
      throw new Error("réseau coupé");
    }, "Demo" as never);
    render(
      <LazyBoundary>
        <Page />
      </LazyBoundary>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Impossible de charger");
    await userEvent.click(screen.getByRole("button", { name: "Recharger la page" }));
    expect(reload).toHaveBeenCalledOnce();
    quiet.mockRestore();
    vi.unstubAllGlobals();
  });
});

describe("Navigation vers une page différée", () => {
  function setup(useTransitions: boolean) {
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const Slow = lazyPage(async () => {
      await gate;
      return { SlowPage };
    }, "SlowPage");
    render(
      <MemoryRouter useTransitions={useTransitions}>
        <Link to="/lente">aller</Link>
        <LazyBoundary>
          <Routes>
            <Route path="/" element={<p>ancienne page</p>} />
            <Route path="/lente" element={<Slow />} />
          </Routes>
        </LazyBoundary>
      </MemoryRouter>,
    );
    return () => release?.();
  }

  it("without transitions the old page is replaced at once by the loading message", async () => {
    const release = setup(false);
    await userEvent.click(screen.getByRole("link", { name: "aller" }));
    expect(screen.getByText("ancienne page")).not.toBeVisible(); // React hides what was on screen while the new page suspends
    expect(screen.getByRole("status")).toHaveTextContent("Chargement");
    release();
    expect(await screen.findByText("page lente")).toBeInTheDocument();
  });

  it("with transitions the old page would stay on screen, and clickable, while the new one downloads (what the application avoids)", async () => {
    const release = setup(true);
    await userEvent.click(screen.getByRole("link", { name: "aller" }));
    expect(screen.getByText("ancienne page")).toBeVisible();
    release();
    expect(await screen.findByText("page lente")).toBeInTheDocument();
  });
});
