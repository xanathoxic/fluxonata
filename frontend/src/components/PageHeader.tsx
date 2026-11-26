import type { ReactNode } from "react";

/** Title, one-line purpose and the page's primary action(s), aligned the same way on every page. */
export function PageHeader({ title, description, actions, sub }: { title: string; description?: ReactNode; actions?: ReactNode; sub?: boolean }) {
  return (
    <header className={sub ? "page-head sub" : "page-head"}>
      <div>
        {sub ? <h2>{title}</h2> : <h1>{title}</h1>}
        {description && <p className="page-desc">{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}
