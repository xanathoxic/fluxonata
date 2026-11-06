import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { DefinitionForm, type LookupFn } from "./DefinitionForm";
import type { FormDefinition } from "./types";

const definition: FormDefinition = {
  version: 1,
  sections: [{ id: "s", title: "Accès", fields: [
    { key: "groupe", type: "select", label: "Groupe", source: { connection: "svc", path: "/acl", items_path: "Groups", items_as: "entries" } },
    { key: "membres", type: "checkbox_list", label: "Membres", source: { connection: "svc", path: "/acl", params: ["groupe"], items_path: "Groups.{groupe}" } },
  ] }],
};
const MEMBERS: Record<string, string[]> = { "group:ops": ["alice", "bruno"], "group:dev": ["carl"] };

function Harness({ lookup }: { lookup: LookupFn }) {
  const [values, setValues] = useState<Record<string, unknown>>({});
  return (<>
    <DefinitionForm definition={definition} values={values} onChange={setValues} lookup={lookup} />
    <output aria-label="valeurs">{JSON.stringify(values)}</output>
  </>);
}
const shown = () => JSON.parse(screen.getByLabelText("valeurs").textContent ?? "{}") as Record<string, unknown>;
const lookupFor = () => vi.fn<LookupFn>(async (key, params) =>
  key === "groupe" ? Object.keys(MEMBERS).map((g) => ({ value: g, label: g }))
    : (MEMBERS[params.groupe ?? ""] ?? []).map((m) => ({ value: m, label: m })));

describe("liste dépendante d'un autre champ (cascade)", () => {
  it("n'interroge pas le serveur tant que le champ lu dans le chemin de la liste est vide, et le dit", async () => {
    const lookup = lookupFor();
    render(<Harness lookup={lookup} />);
    await waitFor(() => expect(lookup).toHaveBeenCalledWith("groupe", {}));
    await act(async () => { await new Promise((r) => setTimeout(r, 450)); });
    expect(lookup.mock.calls.filter(([key]) => key === "membres")).toEqual([]); // aucun appel pour les membres
    expect(screen.getByText(/Les choix apparaîtront quand « groupe » sera renseigné/)).toBeTruthy();
    expect(screen.queryByText(/n'a pas pu être chargée/)).toBeNull();
  });

  it("charge les membres du groupe choisi, puis retire ceux de l'ancien groupe quand on en change", async () => {
    const u = userEvent.setup();
    const lookup = lookupFor();
    render(<Harness lookup={lookup} />);
    await screen.findByRole("option", { name: "group:ops" });
    await u.selectOptions(screen.getByLabelText("Groupe"), "group:ops");
    await u.click(await screen.findByLabelText("alice"));
    await u.click(screen.getByLabelText("bruno"));
    expect(lookup).toHaveBeenCalledWith("membres", { groupe: "group:ops" });
    expect(shown()).toEqual({ groupe: "group:ops", membres: ["alice", "bruno"] });
    await u.selectOptions(screen.getByLabelText("Groupe"), "group:dev");
    await screen.findByLabelText("carl");
    await waitFor(() => expect(shown()).toEqual({ groupe: "group:dev", membres: [] })); // plus aucun membre de « ops » dans la valeur
    expect(screen.queryByLabelText("alice")).toBeNull();
  });
});

describe("chemin composé de plusieurs champs", () => {
  const multi: FormDefinition = {
    version: 1,
    sections: [{ id: "s", title: "Chemin", fields: [
      { key: "champ1", type: "text", label: "Champ 1" },
      { key: "champ2", type: "text", label: "Champ 2" },
      { key: "choix", type: "select", label: "Choix", source: { connection: "svc", path: "/acl/{champ1}/org/{champ2}", params: ["champ1", "champ2"] } },
    ] }],
  };
  function Multi({ lookup, def = multi }: { lookup: LookupFn; def?: FormDefinition }) {
    const [values, setValues] = useState<Record<string, unknown>>({});
    return <DefinitionForm definition={def} values={values} onChange={setValues} lookup={lookup} />;
  }
  const calls = (lookup: ReturnType<typeof lookupFor>) => lookup.mock.calls.filter(([key]) => key === "choix");

  it("n'interroge le service que lorsque les deux champs du chemin sont renseignés", async () => {
    const u = userEvent.setup();
    const lookup = vi.fn<LookupFn>(async () => [{ value: "ok", label: "ok" }]);
    render(<Multi lookup={lookup} />);
    await act(async () => { await new Promise((r) => setTimeout(r, 450)); });
    expect(calls(lookup)).toEqual([]);
    expect(screen.getByText(/Les choix apparaîtront quand « champ1, champ2 » sera renseigné/)).toBeTruthy();
    await u.type(screen.getByLabelText("Champ 1"), "prod");
    await act(async () => { await new Promise((r) => setTimeout(r, 450)); });
    expect(calls(lookup)).toEqual([]); // un seul sur deux : on attend encore
    expect(screen.getByText(/« champ2 » sera renseigné/)).toBeTruthy();
    await u.type(screen.getByLabelText("Champ 2"), "web");
    await waitFor(() => expect(calls(lookup).at(-1)).toEqual(["choix", { champ1: "prod", champ2: "web" }]));
    await screen.findByRole("option", { name: "ok" });
  });

  it("lit aussi la valeur d'un champ CALCULÉ", async () => {
    const lookup = vi.fn<LookupFn>(async () => [{ value: "ok", label: "ok" }]);
    const def: FormDefinition = { version: 1, sections: [{ id: "s", title: "Chemin", fields: [
      { key: "champ1", type: "text", label: "Champ 1", compute: "upper(\"prod\")" },
      { key: "champ2", type: "text", label: "Champ 2", compute: "upper(\"web\")" },
      multi.sections[0]!.fields[2]!,
    ] }] };
    render(<Multi lookup={lookup} def={def} />);
    await waitFor(() => expect(calls(lookup).at(-1)).toEqual(["choix", { champ1: "PROD", champ2: "WEB" }]));
  });

  it("un {champ} placé après le « ? » reste envoyé même vide (« tous »)", async () => {
    const lookup = vi.fn<LookupFn>(async () => []);
    const def: FormDefinition = { version: 1, sections: [{ id: "s", title: "Q", fields: [
      { key: "marque", type: "text", label: "Marque" },
      { key: "choix", type: "select", label: "Choix", source: { connection: "svc", path: "/modeles?marque={marque}", params: ["marque"] } },
    ] }] };
    render(<Multi lookup={lookup} def={def} />);
    await waitFor(() => expect(calls(lookup)[0]).toEqual(["choix", { marque: "" }]));
  });

  it("même attente pour un contrôle personnalisé dont les options viennent d'un service", async () => {
    const u = userEvent.setup();
    const lookup = vi.fn<LookupFn>(async () => [{ value: "ok", label: "ok" }]);
    const def: FormDefinition = { version: 1, sections: [{ id: "s", title: "Contrôle", fields: [
      { key: "champ1", type: "text", label: "Champ 1" },
      { key: "ctl", type: "custom", label: "Contrôle", control: { ref: "choix-service", version: "1.0.0", name: "Choix", spec: { version: 1, mode: "choice", parts: [], dynamic: true } },
        source: { connection: "svc", path: "/acl/{champ1}", params: ["champ1"] } },
    ] }] };
    render(<Multi lookup={lookup} def={def} />);
    await act(async () => { await new Promise((r) => setTimeout(r, 450)); });
    expect(lookup.mock.calls.filter(([key]) => key === "ctl")).toEqual([]);
    expect(screen.getByText(/Les options apparaîtront quand « champ1 » sera renseigné/)).toBeTruthy();
    await u.type(screen.getByLabelText("Champ 1"), "prod");
    await waitFor(() => expect(lookup).toHaveBeenCalledWith("ctl", { champ1: "prod" }));
  });

  it("une liste dont la connexion dépend d'un champ attend ce champ, puis envoie sa valeur au serveur", async () => {
    const u = userEvent.setup();
    const lookup = vi.fn<LookupFn>(async () => [{ value: "ok", label: "ok" }]);
    const def: FormDefinition = { version: 1, sections: [{ id: "s", title: "Env", fields: [
      { key: "env", type: "text", label: "Environnement" },
      { key: "choix", type: "select", label: "Choix", source: { connection: "", path: "/acl", params: ["env"], connection_from: "env", connections: { prod: "tailnet-prod", test: "tailnet-test" } } },
    ] }] };
    render(<Multi lookup={lookup} def={def} />);
    await act(async () => { await new Promise((r) => setTimeout(r, 450)); });
    expect(calls(lookup)).toEqual([]);
    expect(screen.getByText(/Les choix apparaîtront quand « env » sera renseigné/)).toBeTruthy();
    await u.type(screen.getByLabelText("Environnement"), "prod");
    await waitFor(() => expect(calls(lookup).at(-1)).toEqual(["choix", { env: "prod" }]));
  });
});
