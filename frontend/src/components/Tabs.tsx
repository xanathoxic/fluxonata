import { useId, type ReactNode } from "react";
import { PageTabs, type PageTab } from "./PageTabs";

export interface TabDef extends Omit<PageTab, "to"> {
  content: ReactNode;
}

/** Page tabs with their panels (only the active one is rendered). The bar itself is `PageTabs`. */
export function Tabs({ tabs, active, onChange, label, className }: { tabs: TabDef[]; active: string; onChange: (id: string) => void; label: string; className?: string }) {
  const base = useId();
  return (
    <>
      <PageTabs tabs={tabs} active={active} label={label} className={className} onChange={onChange} tabId={(id) => `${base}-t-${id}`} panelId={(id) => `${base}-p-${id}`} />
      {tabs.map((t) => (
        <div key={t.id} id={`${base}-p-${t.id}`} role="tabpanel" aria-labelledby={`${base}-t-${t.id}`} hidden={t.id !== active} className="tabpanel">
          {t.id === active && t.content}
        </div>
      ))}
    </>
  );
}
