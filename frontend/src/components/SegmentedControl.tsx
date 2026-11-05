import { useId } from "react";
import { FormIcon, type FormIconName } from "../forms/FormIcon";

export interface Segment { id: string; label: string; icon?: FormIconName }

/** Switch between MODES of the same content inside a tab (visual/code, editor/style/preview). Never for the tabs of a page: that is `PageTabs`. */
export function SegmentedControl({ segments, active, onChange, label }: { segments: Segment[]; active: string; onChange: (id: string) => void; label: string }) {
  const base = useId();
  const idx = Math.max(0, segments.findIndex((t) => t.id === active));
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const next = segments[(idx + (e.key === "ArrowRight" ? 1 : segments.length - 1)) % segments.length] as Segment;
    onChange(next.id);
    document.getElementById(`${base}-t-${next.id}`)?.focus();
  };
  return (
    <div role="tablist" aria-label={label} onKeyDown={onKey} className="fb-seg">
      {segments.map((t) => (
        <button key={t.id} id={`${base}-t-${t.id}`} role="tab" type="button" aria-selected={t.id === active} tabIndex={t.id === active ? 0 : -1} onClick={() => onChange(t.id)}>
          {t.icon && <FormIcon name={t.icon} />}<span className="fb-seg-t">{t.label}</span>
        </button>
      ))}
    </div>
  );
}
