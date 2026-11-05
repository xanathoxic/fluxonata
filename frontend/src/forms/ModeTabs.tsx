import { SegmentedControl } from "../components/SegmentedControl";
import type { FormIconName } from "./FormIcon";

export interface ModeTab { id: string; label: string; icon: FormIconName }

/** Kept for the existing call sites: a mode switch is a `SegmentedControl`. */
export function ModeTabs({ tabs, active, onChange, label }: { tabs: ModeTab[]; active: string; onChange: (id: string) => void; label: string }) {
  return <SegmentedControl segments={tabs} active={active} onChange={onChange} label={label} />;
}
