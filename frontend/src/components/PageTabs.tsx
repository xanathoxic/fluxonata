import { useId, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { FormIcon, type FormIconName } from "../forms/FormIcon";

export interface PageTab {
  id: string;
  label: ReactNode;
  icon?: FormIconName;
  /** A count or a short mark (« ! ») after the label; `alert` draws it as a warning. */
  badge?: number | string;
  badgeTone?: "alert";
  /** Route of the tab: when every tab has one, the bar is a navigation of links (deep links, middle-click, aria-current) instead of a tablist. */
  to?: string;
}

interface Props {
  tabs: PageTab[];
  active: string;
  label: string;
  onChange?: (id: string) => void;
  className?: string;
  /** Id of the tab -> id of its panel, when the panels are rendered by the caller. */
  panelId?: (id: string) => string;
  tabId?: (id: string) => string;
}

const inner = (t: PageTab) => (
  <>
    {t.icon && <FormIcon name={t.icon} size={16} />}
    <span className="pt-label">{t.label}</span>
    {t.badge !== undefined && t.badge !== "" && <span className={`pt-badge${t.badgeTone === "alert" ? " pt-alert" : ""}`}>{t.badge}</span>}
  </>
);

/** The only page-level tab bar: flat text with an accent line under the active one, optional icon and badge. Driven by state (tablist, arrow keys)
 *  or by the route (links with aria-current). Scrolls sideways on a narrow screen, with a fade at the edge that hides the rest. */
export function PageTabs({ tabs, active, label, onChange, className = "", panelId, tabId }: Props) {
  const base = useId();
  const idx = Math.max(0, tabs.findIndex((t) => t.id === active));
  const routed = tabs.length > 0 && tabs.every((t) => t.to);
  const cls = `tabs pt${className ? ` ${className}` : ""}`;

  if (routed) {
    return (
      <nav aria-label={label} className={cls}>
        <ul>{tabs.map((t) => (
          <li key={t.id}><Link to={t.to as string} className={t.id === active ? "active" : ""} aria-current={t.id === active ? "page" : undefined}>{inner(t)}</Link></li>
        ))}</ul>
      </nav>
    );
  }
  const onKey = (e: React.KeyboardEvent) => {
    const keys: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1 };
    if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      const t = tabs[e.key === "Home" ? 0 : tabs.length - 1] as PageTab;
      onChange?.(t.id);
      document.getElementById(tabId?.(t.id) ?? `${base}-t-${t.id}`)?.focus();
      return;
    }
    const by = keys[e.key];
    if (by === undefined) return;
    e.preventDefault();
    const next = tabs[(idx + by + tabs.length) % tabs.length] as PageTab;
    onChange?.(next.id);
    document.getElementById(tabId?.(next.id) ?? `${base}-t-${next.id}`)?.focus();
  };
  return (
    <div role="tablist" aria-label={label} onKeyDown={onKey} className={cls}>
      {tabs.map((t) => (
        <button key={t.id} id={tabId?.(t.id) ?? `${base}-t-${t.id}`} role="tab" type="button" aria-selected={t.id === active} aria-controls={panelId?.(t.id)}
          tabIndex={t.id === active ? 0 : -1} onClick={() => onChange?.(t.id)}>{inner(t)}</button>
      ))}
    </div>
  );
}
