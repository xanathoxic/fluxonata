import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { FormIcon } from "../forms/FormIcon";
import { Tooltip } from "./Tooltip";

/** The header shared by the three editors (workflow, form, control): ONE line that stays put, whatever the tab —
 *  [←] title · status line · actions — then the tab bar. Notices (conflict, recovery, problems) go BELOW the tabs, so the bar never moves. */
export function EditorShell({ backTo, backLabel, icon, title, titleHint, titleExtra, status, actions, tabs, notices, children, fill = "fb", actionsLabel = "Actions", className = "" }: {
  backTo: string;
  backLabel: string;
  icon?: ReactNode;
  title: string;
  titleHint?: string;
  titleExtra?: ReactNode;
  status: ReactNode;
  actions?: ReactNode;
  tabs?: ReactNode;
  notices?: ReactNode;
  children: ReactNode;
  /** "fb": the page fills the window and the tab panel scrolls; "wf": same for the workflow canvas; `false`: the page scrolls. */
  fill?: false | "wf" | "fb";
  actionsLabel?: string;
  className?: string;
}) {
  return (
    <div className={`es${fill === "fb" ? " wf-fill fb-fill" : fill === "wf" ? " wf-fill" : " wf-page"}${className ? ` ${className}` : ""}`}>
      <div className="es-top">
        <header className="es-head">
          <Tooltip text={backLabel}><Link to={backTo} className="icon-btn neutral-icon" aria-label={backLabel}><FormIcon name="back" /></Link></Tooltip>
          {icon}
          <h1 className="es-title" title={titleHint}>{title}</h1>
          {titleExtra}
          {status}
          {actions && <div className="es-actions" role="toolbar" aria-label={actionsLabel}>{actions}</div>}
        </header>
        {tabs}
      </div>
      {notices && <div className="es-notices">{notices}</div>}
      {children}
    </div>
  );
}
