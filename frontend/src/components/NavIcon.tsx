const PATHS: Record<string, React.ReactElement> = {
  "/home": <path d="M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10" />,
  "/catalog": <><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>,
  "/workflows": <><circle cx="5" cy="6" r="2.5" /><circle cx="19" cy="6" r="2.5" /><circle cx="12" cy="18" r="2.5" /><path d="M7.5 6h9M6 8.5l5 7M18 8.5l-5 7" /></>,
  "/library": <path d="M4 4h4v16H4zM10 4h4v16h-4zM16.5 5.5l3.8-1 3 15-3.8 1z" />,
  "/library/actions": <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12v9M12 12L4 7.5" />,
  "/library/skills": <path d="M12 3l2.2 5.8L20 11l-5.8 2.2L12 19l-2.2-5.8L4 11l5.8-2.2z" />,
  "/library/forms": <><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h5" /></>,
  "/library/controls": <path d="M10 3h4v3a2 2 0 1 0 0 4v3h-3a2 2 0 1 1-4 0H4V9h3a2 2 0 1 0 0-4V3zM14 13h6v8h-8v-4a2 2 0 1 0 2-4z" />,
  "/connections": <><path d="M9 3v5M15 3v5M6 8h12v4a6 6 0 0 1-12 0z" /><path d="M12 18v3" /></>,
  "/triggers": <path d="M13 2L4 14h7l-1 8 9-12h-7z" />,
  "/executions": <><circle cx="12" cy="12" r="9" /><path d="M10 8.5l5 3.5-5 3.5z" /></>,
  "/ops": <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />,
  "/approvals": <><circle cx="12" cy="12" r="9" /><path d="M7.5 12.5l3 3 6-6.5" /></>,
  "/platform": <><rect x="3" y="4" width="18" height="6" rx="1.5" /><rect x="3" y="14" width="18" height="6" rx="1.5" /><path d="M7 7h.01M7 17h.01" /></>,
  "/admin": <><circle cx="12" cy="12" r="3" /><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1" /></>,
};

/** Decorative: the link's accessible name is its visible text. */
export function NavIcon({ to }: { to: string }) {
  return (
    <svg className="nav-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {PATHS[to]}
    </svg>
  );
}
