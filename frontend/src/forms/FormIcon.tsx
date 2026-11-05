import type { ReactElement } from "react";
import { Tooltip } from "../components/Tooltip";
import type { FieldType } from "./types";
import "./forms.css";

const PATHS = {
  // field types
  text: <path d="M5 6h14M12 6v13M9 19h6" />,
  textarea: <path d="M4 6h16M4 11h16M4 16h10" />,
  number: <path d="M9 4L7 20M17 4l-2 16M4 9h16M3 15h16" />,
  email: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 7l9 6 9-6" /></>,
  date: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M3 10h18M8 3v4M16 3v4" /></>,
  select: <><rect x="3" y="6" width="18" height="12" rx="2" /><path d="M9 11l3 3 3-3" /></>,
  radio: <><circle cx="12" cy="12" r="8" /><circle cx="12" cy="12" r="3" fill="currentColor" /></>,
  combobox: <><circle cx="10" cy="10" r="5.5" /><path d="M14.5 14.5L20 20" /></>,
  checkbox: <><rect x="4" y="4" width="16" height="16" rx="3" /><path d="M8 12.5l3 3 5-6" /></>,
  checkbox_list: <path d="M4 6l1.5 1.5L8 5M4 13l1.5 1.5L8 12M4 20l1.5 1.5L8 19M12 6h8M12 13h8M12 20h8" />,
  datagrid: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 10h18M3 15h18M9 4v16M15 4v16" /></>,
  // custom controls
  custom: <path d="M10 3h4v3a2 2 0 1 0 0 4v3h-3a2 2 0 1 1-4 0H4V9h3a2 2 0 1 0 0-4V3zM14 13h6v8h-8v-4a2 2 0 1 0 2-4z" />,
  repeat: <path d="M17 2l4 4-4 4M3 11V9a3 3 0 0 1 3-3h15M7 22l-4-4 4-4M21 13v2a3 3 0 0 1-3 3H3" />,
  boxes: <><rect x="3" y="4" width="6" height="6" rx="1" /><path d="M4.5 7l1.2 1.2L8 5.8M12 7h9" /><rect x="3" y="14" width="6" height="6" rx="1" /><path d="M12 17h9" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 21c0-4 3.5-7 8-7s8 3 8 7" /></>,
  mapPin: <><path d="M12 21s7-6.2 7-11a7 7 0 0 0-14 0c0 4.800 7 11 7 11z" /><circle cx="12" cy="10" r="2.5" /></>,
  phone: <path d="M5 4h4l2 5-2.500 1.500a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z" />,
  euro: <path d="M18 6.500A7 7 0 1 0 18 17.500M4 10h9M4 14h9" />,
  cart: <><circle cx="9" cy="20" r="1.500" /><circle cx="18" cy="20" r="1.500" /><path d="M2 3h3l2.500 12h11L21 7H6" /></>,
  building: <path d="M5 21V4h9v17M14 9h5v12M3 21h18M8 8h3M8 12h3M8 16h3" />,
  file: <path d="M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6" />,
  star: <path d="M12 3l2.700 5.600 6.100.9-4.400 4.300 1 6.100L12 17l-5.400 2.900 1-6.100L3.200 9.500l6.100-.9z" />,
  heart: <path d="M12 20s-8-4.800-8-11a4.500 4.500 0 0 1 8-2.800A4.500 4.500 0 0 1 20 9c0 6.200-8 11-8 11z" />,
  tag: <><path d="M3 12V4h8l10 10-8 8z" /><circle cx="7.500" cy="8.500" r="1.200" /></>,
  // content blocks
  heading: <path d="M6 4v16M18 4v16M6 12h12" />,
  paragraph: <path d="M13 4v16M9 4v16M13 4H9a4 4 0 0 0 0 8h4" />,
  divider: <path d="M3 12h18M7 7l-2-2M17 7l2-2M7 17l-2 2M17 17l2 2" />,
  // placement
  taller: <path d="M12 4v16M9 7l3-3 3 3M9 17l3 3 3-3" />,
  shorter: <path d="M12 4v5M12 15v5M9 6l3 3 3-3M9 18l3-3 3 3" />,
  right: <path d="M5 12h14M13 6l6 6-6 6" />,
  wider: <path d="M4 12h16M8 8l-4 4 4 4M16 8l4 4-4 4" />,
  narrower: <path d="M3 12h6M21 12h-6M6 9l3 3-3 3M18 9l-3 3 3 3" />,
  palette: <path d="M12 3a9 9 0 1 0 0 18c1.5 0 2-1 2-2s-.5-1.5-.5-2.5S15 15 16.5 15H19a2 2 0 0 0 2-2c0-5-4-10-9-10zM7.5 11h.01M10 7.5h.01M15 7.5h.01" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  pointer: <path d="M5 3l14 7-6 2-2 6z" />,
  copyCss: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 0 1 2-2h9" /></>,
  type: <path d="M4 7V5h16v2M12 5v14M9 19h6" />,
  fill: <path d="M5 11l7-7 7 7-7 7zM19 15c1.5 2 2 3 2 4a2 2 0 0 1-4 0c0-1 .5-2 2-4z" />,
  border: <rect x="4" y="4" width="16" height="16" rx="4" strokeDasharray="3 2" />,
  spacing: <><rect x="3" y="3" width="18" height="18" rx="2" /><rect x="8" y="8" width="8" height="8" rx="1" /></>,
  size: <path d="M4 20V4M4 4l4 4M4 4l-0 0M20 20H4M20 20l-4-4M9 15l6-6" />,
  shadow: <><rect x="4" y="4" width="12" height="12" rx="2" /><path d="M8 20h12V8" /></>,
  layout: <><rect x="3" y="3" width="8" height="8" rx="1" /><rect x="13" y="3" width="8" height="8" rx="1" /><rect x="3" y="13" width="18" height="8" rx="1" /></>,
  effects: <path d="M12 3l1.8 4.6L18 9l-4.2 1.4L12 15l-1.8-4.6L6 9l4.2-1.4zM19 15l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z" />,
  sliders: <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0M14 4v4M8 10v4M16 16v4" />,
  eyeOff: <path d="M3 3l18 18M10.6 6.2A9.8 9.8 0 0 1 12 6c6 0 9 6 9 6a15 15 0 0 1-3.2 3.9M6.1 6.9A14.5 14.5 0 0 0 3 12s3 6 9 6a9 9 0 0 0 3.2-.6" />,
  alignL: <path d="M4 6h16M4 10h10M4 14h16M4 18h10" />,
  alignC: <path d="M4 6h16M7 10h10M4 14h16M7 18h10" />,
  alignR: <path d="M4 6h16M10 10h10M4 14h16M10 18h10" />,
  alignJ: <path d="M4 6h16M4 10h16M4 14h16M4 18h16" />,
  rowDir: <path d="M4 12h16M15 7l5 5-5 5" />,
  colDir: <path d="M12 4v16M7 15l5 5 5-5" />,
  lock: <><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></>,
  unlock: <><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 7-2.6" /></>,
  list2: <path d="M8 6h13M8 12h13M8 18h13M3 6h2M3 12h2M3 18h2" />,
  pause2: <path d="M8 5v14M16 5v14" />,
  // toolbar
  redo: <path d="M15 14l5-5-5-5M20 9H10a6 6 0 0 0 0 12h3" />,
  search: <><circle cx="10.5" cy="10.5" r="5.5" /><path d="M15 15l5 5" /></>,
  braces: <path d="M8 4c-2 0-3 1-3 3v2c0 1-1 2-2 3 1 1 2 2 2 3v2c0 2 1 3 3 3M16 4c2 0 3 1 3 3v2c0 1 1 2 2 3-1 1-2 2-2 3v2c0 2-1 3-3 3" />,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8v.01" /></>,
  sort: <path d="M8 9l4-4 4 4M8 15l4 4 4-4" />,
  help: <><circle cx="12" cy="12" r="9" /><path d="M9.6 9.4a2.5 2.5 0 1 1 3.6 2.3c-.8.4-1.2 1-1.2 1.8M12 16.8v.01" /></>,
  warn: <path d="M12 3l10 18H2zM12 10v5M12 18v.01" />,
  // style: density, labels, corners, width, sections, button alignment
  dense1: <><rect x="5" y="4" width="14" height="4" rx="1" /><rect x="5" y="10" width="14" height="4" rx="1" /><rect x="5" y="16" width="14" height="4" rx="1" /></>,
  dense2: <><rect x="5" y="4" width="14" height="3" rx="1" /><rect x="5" y="10.5" width="14" height="3" rx="1" /><rect x="5" y="17" width="14" height="3" rx="1" /></>,
  dense3: <><rect x="5" y="3" width="14" height="4" rx="1" /><rect x="5" y="17" width="14" height="4" rx="1" /></>,
  labelTop: <><path d="M5 6h8" /><rect x="5" y="10" width="14" height="8" rx="1.5" /></>,
  labelLeft: <><path d="M3 12h5" /><rect x="11" y="8" width="10" height="8" rx="1.5" /></>,
  corner0: <path d="M5 19V5h14" />,
  corner1: <path d="M5 19V8q0-3 3-3h11" />,
  corner2: <path d="M5 19V10q0-5 5-5h9" />,
  corner3: <path d="M5 19V14q0-9 9-9h5" />,
  width1: <rect x="8" y="5" width="8" height="14" rx="1.5" />,
  width2: <rect x="6" y="5" width="12" height="14" rx="1.5" />,
  width3: <rect x="4" y="5" width="16" height="14" rx="1.5" />,
  width4: <path d="M3 5v14M21 5v14M7 12h10M10 9l-3 3 3 3M14 9l3 3-3 3" />,
  secCard: <><rect x="4" y="5" width="16" height="14" rx="2" /><path d="M8 10h8M8 14h5" /></>,
  secPlain: <path d="M5 8h14M5 12h14M5 16h9" />,
  secLine: <path d="M4 6h16M6 11h12M6 15h12M6 19h8" />,
  btnLeft: <rect x="4" y="9" width="8" height="6" rx="1.5" />,
  btnCenter: <rect x="8" y="9" width="8" height="6" rx="1.5" />,
  btnRight: <rect x="12" y="9" width="8" height="6" rx="1.5" />,
  btnFull: <rect x="4" y="9" width="16" height="6" rx="1.5" />,
  // form modes
  simple: <path d="M5 6h14M5 12h14M5 18h14" />,
  steps: <path d="M3 19h6v-5h6V9h6V4" />,
  design: <><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18M9 9v12" /></>,
  // actions
  up: <path d="M12 19V5M6 11l6-6 6 6" />,
  down: <path d="M12 5v14M6 13l6 6 6-6" />,
  trash: <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />,
  plus: <path d="M12 5v14M5 12h14" />,
  save: <><path d="M5 4h11l3 3v13H5z" /><path d="M8 4v5h7V4M8 20v-6h8v6" /></>,
  send: <path d="M21 3L10 14M21 3l-7 18-4-7-7-4z" />,
  undo: <path d="M9 14L4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3" />,
  back: <path d="M19 12H5M11 6l-6 6 6 6" />,
  eye: <><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></>,
  edit: <path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4" />,
  wand: <path d="M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2z" />,
  rule: <path d="M4 5h16l-6 8v6l-4-2v-4z" />,
  code: <path d="M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16" />,
  section: <><rect x="3" y="4" width="18" height="6" rx="1.5" /><rect x="3" y="14" width="18" height="6" rx="1.5" /></>,
  source: <><ellipse cx="12" cy="6" rx="7" ry="3" /><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  check: <><circle cx="12" cy="12" r="9" /><path d="M8 12.5l3 3 5-6" /></>,
  play: <path d="M7 4.5v15l12-7.5z" />,
  copy: <><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 0 1 2-2h9" /></>,
  link: <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />,
  list: <path d="M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01" />,
  layers: <path d="M12 3l9 5-9 5-9-5zM3 13l9 5 9-5" />,
} satisfies Record<string, ReactElement>;

export type FormIconName = keyof typeof PATHS;
export const fieldIcon = (t: FieldType): FormIconName => t;

/** Decorative line icon: the accessible name always comes from the button's label or visible text, never from the icon. */
export function FormIcon({ name, size = 18, className }: { name: FormIconName; size?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill={name === "play" ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {PATHS[name]}
    </svg>
  );
}

/** A button that is only an icon (name and tooltip from `label`). */
export function IconButton({ icon, label, onClick, disabled, danger, type = "button" }: { icon: FormIconName; label: string; onClick?: () => void; disabled?: boolean; danger?: boolean; type?: "button" | "submit" }) {
  return <Tooltip text={label}><button type={type} className={`fb-ibtn${danger ? " fb-del" : ""}`} aria-label={label} onClick={onClick} disabled={disabled}><FormIcon name={icon} /></button></Tooltip>;
}

/** Icon followed by a visible text, for the buttons whose label matters. */
export function IconLabel({ icon, children }: { icon: FormIconName; children: React.ReactNode }) {
  return <span className="fb-il"><FormIcon name={icon} size={16} />{children}</span>;
}
