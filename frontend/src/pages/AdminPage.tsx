import { useState } from "react";
import { Tabs } from "../components/Tabs";
import { useApp } from "../context";
import { AiTab } from "./admin/AiTab";
import { AuditTab } from "./admin/AuditTab";
import { HealthTab } from "./admin/HealthTab";
import { MembersTab } from "./admin/MembersTab";
import { PolicyTab } from "./admin/PolicyTab";
import { TokensTab } from "./admin/TokensTab";
import { RunnersPanel } from "./runners/RunnersPanel";

export function AdminPage() {
  const { can } = useApp();
  const [tab, setTab] = useState("health");
  if (!can("administer")) return <p role="alert">L'administration est réservée aux administrateurs et responsables d'espace.</p>;
  return (
    <>
      <h1>Administration</h1>
      <Tabs label="Sections d'administration" active={tab} onChange={setTab} tabs={[
        { id: "health", label: "Santé", content: <HealthTab /> },
        { id: "members", label: "Membres", content: <MembersTab /> },
        { id: "tokens", label: "Jetons et comptes de service", content: <TokensTab /> },
        { id: "runners", label: "Runners", content: <RunnersPanel mode="workspace" /> },
        { id: "policy", label: "Politiques", content: <PolicyTab /> },
        { id: "audit", label: "Audit", content: <AuditTab /> },
        { id: "ai", label: "Assistant IA", content: <AiTab /> },
      ]} />
    </>
  );
}
