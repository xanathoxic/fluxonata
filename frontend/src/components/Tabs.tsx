import { useId, type ReactNode } from "react";

export interface TabDef {
  id: string;
  label: string;
  content: ReactNode;
}

/** Accessible tabs (roving selection with arrow keys, roles and panel association). */
export function Tabs({ tabs, active, onChange, label }: { tabs: TabDef[]; active: string; onChange: (id: string) => void; label: string }) {
  const base = useId();
  const idx = Math.max(0, tabs.findIndex((t) => t.id === active));
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const next = (idx + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
    onChange((tabs[next] as TabDef).id);
    document.getElementById(`${base}-t-${(tabs[next] as TabDef).id}`)?.focus();
  };
  return (
    <>
      <div role="tablist" aria-label={label} onKeyDown={onKey} className="tabs">
        {tabs.map((t) => (
          <button key={t.id} id={`${base}-t-${t.id}`} role="tab" type="button" aria-selected={t.id === active} aria-controls={`${base}-p-${t.id}`}
            tabIndex={t.id === active ? 0 : -1} onClick={() => onChange(t.id)}>{t.label}</button>
        ))}
      </div>
      {tabs.map((t) => (
        <div key={t.id} id={`${base}-p-${t.id}`} role="tabpanel" aria-labelledby={`${base}-t-${t.id}`} hidden={t.id !== active} className="tabpanel">
          {t.id === active && t.content}
        </div>
      ))}
    </>
  );
}
