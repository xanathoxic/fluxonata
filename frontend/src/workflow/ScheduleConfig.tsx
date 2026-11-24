import { useId, useMemo } from "react";
import { HelpTip } from "../components/HelpTip";
import type { GraphNode } from "./types";
import { DEFAULT_TZ, DELAY_UNITS, isExpression, relative, splitDelay, tomorrowAt8, validZone, zonedToUtc, zoneNames } from "./scheduleTime";

const fmt = (ms: number, tz: string): string => new Intl.DateTimeFormat("fr-FR", { timeZone: tz, dateStyle: "full", timeStyle: "short" }).format(new Date(ms));
const fmtUtc = (ms: number): string => new Intl.DateTimeFormat("fr-FR", { timeZone: "UTC", dateStyle: "short", timeStyle: "short" }).format(new Date(ms));

/** Quand lancer le workflow programmé : une date et une heure (dans un fuseau), ou un délai ; et que faire si la date est déjà passée. Le choix du workflow et de ses entrées se fait dans le même panneau (comme un sous-workflow). */
export function ScheduleWhen({ node, onUpdate, now = Date.now() }: { node: GraphNode; onUpdate: (patch: Partial<GraphNode>) => void; now?: number }) {
  const id = useId();
  const when = node.when ?? "at";
  const tz = node.timezone || DEFAULT_TZ;
  const at = node.at ?? "";
  const dynamic = isExpression(at);
  const zones = useMemo(() => zoneNames(), []);
  const target = !dynamic && when === "at" ? zonedToUtc(at, tz) : null;
  const delay = splitDelay(node.delay_seconds);
  return (
    <>
      <div className="field">
        <label htmlFor={`${id}-when`}>Quand lancer</label>
        <select id={`${id}-when`} value={when} onChange={(e) => onUpdate({ when: e.target.value === "in" ? "in" : "at" })}>
          <option value="at">À une date et une heure précises</option>
          <option value="in">Après un délai</option>
        </select>
      </div>
      {when === "at" && (
        <>
          <div className="field">
            <HelpTip>
              La date et l'heure sont celles du fuseau choisi ci-dessous. Pour une date qui change à chaque exécution, cochez « Date calculée » et saisissez une
              expression, ex. <code>{"{{ inputs.date_rappel }}"}</code> (au format <code>2026-12-01T08:30</code>).
            </HelpTip>
            <label htmlFor={`${id}-at`}>{dynamic ? "Date et heure (expression)" : "Date et heure"}</label>
            {dynamic ? (
              <input id={`${id}-at`} list="expr-paths" value={at} placeholder="{{ inputs.date_rappel }}" onChange={(e) => onUpdate({ at: e.target.value })} />
            ) : (
              <input id={`${id}-at`} type="datetime-local" value={at.slice(0, 16)} onChange={(e) => onUpdate({ at: e.target.value })} />
            )}
          </div>
          <div className="field field-inline">
            <input id={`${id}-dyn`} type="checkbox" checked={dynamic} onChange={(e) => onUpdate({ at: e.target.checked ? "{{ inputs.date }}" : "" })} />
            <label htmlFor={`${id}-dyn`}>Date calculée (expression)</label>
          </div>
          <div className="field">
            <label htmlFor={`${id}-tz`}>Fuseau horaire</label>
            <input
              id={`${id}-tz`}
              list={`${id}-zones`}
              value={node.timezone ?? DEFAULT_TZ}
              aria-invalid={!validZone(tz)}
              onChange={(e) => onUpdate({ timezone: e.target.value })}
            />
            <datalist id={`${id}-zones`}>
              {zones.map((z) => (
                <option key={z} value={z} />
              ))}
            </datalist>
            {!validZone(tz) && <p className="field-error">Fuseau inconnu (ex. Europe/Paris, UTC).</p>}
          </div>
          {target !== null && (
            <p className="hint" role="status">
              {target <= now ? (
                <strong>Cette date est déjà passée.</strong>
              ) : (
                <>
                  Sera lancé le <strong>{fmt(target, tz)}</strong> ({tz}), {relative(target, now)}
                  {tz === "UTC" ? "." : ` — soit ${fmtUtc(target)} UTC.`}
                </>
              )}
            </p>
          )}
        </>
      )}
      {when === "in" && (
        <div className="field">
          <HelpTip>Le délai court à partir du moment où cette étape s'exécute. Il peut aller de 1 seconde à 366 jours.</HelpTip>
          <label htmlFor={`${id}-delay`}>Délai</label>
          <div className="inline-fields">
            <input
              id={`${id}-delay`}
              type="number"
              min={1}
              value={delay.value}
              onChange={(e) => onUpdate({ delay_seconds: e.target.value === "" ? undefined : Number(e.target.value) * delay.unit })}
            />
            <select
              aria-label="Unité du délai"
              value={delay.unit}
              onChange={(e) => onUpdate({ delay_seconds: (Number(delay.value) || 1) * Number(e.target.value) })}
            >
              {DELAY_UNITS.map((u) => (
                <option key={u.seconds} value={u.seconds}>
                  {u.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}
      <div className="field">
        <HelpTip>
          Une date passée peut venir d'une expression calculée. « Échouer » arrête l'étape (branche d'erreur) ; « lancer tout de suite » démarre le workflow au
          prochain passage de l'orchestrateur.
        </HelpTip>
        <label htmlFor={`${id}-past`}>Si la date est déjà passée</label>
        <select id={`${id}-past`} value={node.on_past ?? "fail"} onChange={(e) => onUpdate({ on_past: e.target.value === "now" ? "now" : "fail" })}>
          <option value="fail">Échouer (branche d'erreur)</option>
          <option value="now">Lancer tout de suite</option>
        </select>
      </div>
    </>
  );
}

/** Rappel affiché sous les entrées : ce que fait vraiment l'étape. */
export function ScheduleNote({ node }: { node: GraphNode }) {
  return (
    <p className="hint">
      Cette étape <strong>ne lance rien maintenant</strong> : elle programme le lancement de <strong>{node.workflow || "ce workflow"}</strong> et le workflow en
      cours continue. Les lancements en attente se voient et s'annulent dans <em>Déclencheurs → Lancements programmés</em>. Le lancement se fait avec votre
      identité et les règles de l'espace (promotion en production) sont revérifiées à l'heure dite. Sorties : <code>steps.{node.id}.output.run_at</code>,{" "}
      <code>.id</code>.
    </p>
  );
}

/** « Attente » : durée, ou pause jusqu'à une date et une heure (le workflow reprend alors tout seul, même après un redémarrage). */
export function WaitUntil({
  node,
  onUpdate,
  now = Date.now(),
  deadline = false,
}: {
  node: GraphNode;
  onUpdate: (patch: Partial<GraphNode>) => void;
  now?: number;
  deadline?: boolean;
}) {
  const id = useId();
  const until = node.until;
  const tz = node.timezone || DEFAULT_TZ;
  const dynamic = isExpression(until ?? "");
  const zones = useMemo(() => zoneNames(), []);
  const target = until !== undefined && !dynamic ? zonedToUtc(until, tz) : null;
  const noun = deadline ? "Échéance" : "Durée";
  return (
    <>
      <div className="field">
        <label htmlFor={`${id}-mode`}>{deadline ? "Délai maximum" : "Mettre en pause"}</label>
        <select
          id={`${id}-mode`}
          value={until === undefined ? "for" : "until"}
          onChange={(e) =>
            onUpdate(e.target.value === "until" ? { until: node.until ?? tomorrowAt8(), timezone: node.timezone ?? DEFAULT_TZ } : { until: undefined })
          }
        >
          <option value="for">Pendant une durée</option>
          <option value="until">Jusqu'à une date et une heure</option>
        </select>
      </div>
      {until === undefined ? (
        <div className="field">
          <HelpTip>L'attente est durable : elle survit à un redémarrage et ne mobilise aucun worker.</HelpTip>
          <label htmlFor={`${id}-s`}>{noun} (secondes)</label>
          <input id={`${id}-s`} type="number" min={1} value={node.seconds ?? 60} onChange={(e) => onUpdate({ seconds: Number(e.target.value) })} />
        </div>
      ) : (
        <>
          <div className="field">
            <HelpTip>
              Le workflow est mis en pause et reprend à cette date, dans le fuseau choisi, même si la plate-forme a redémarré entre-temps (au plus 366 jours).
              Pour une date qui change à chaque exécution, cochez « Date calculée » : <code>{"{{ inputs.date_reprise }}"}</code> au format{" "}
              <code>2026-12-01T08:30</code>.
            </HelpTip>
            <label htmlFor={`${id}-at`}>{dynamic ? "Date et heure de reprise (expression)" : "Reprendre le"}</label>
            {dynamic ? (
              <input
                id={`${id}-at`}
                list="expr-paths"
                value={until}
                placeholder="{{ inputs.date_reprise }}"
                onChange={(e) => onUpdate({ until: e.target.value })}
              />
            ) : (
              <input id={`${id}-at`} type="datetime-local" value={(until ?? "").slice(0, 16)} onChange={(e) => onUpdate({ until: e.target.value })} />
            )}
          </div>
          <div className="field field-inline">
            <input
              id={`${id}-dyn`}
              type="checkbox"
              checked={dynamic}
              onChange={(e) => onUpdate({ until: e.target.checked ? "{{ inputs.date }}" : tomorrowAt8() })}
            />
            <label htmlFor={`${id}-dyn`}>Date calculée (expression)</label>
          </div>
          <div className="field">
            <label htmlFor={`${id}-tz`}>Fuseau horaire</label>
            <input
              id={`${id}-tz`}
              list={`${id}-zones`}
              value={node.timezone ?? DEFAULT_TZ}
              aria-invalid={!validZone(tz)}
              onChange={(e) => onUpdate({ timezone: e.target.value })}
            />
            <datalist id={`${id}-zones`}>
              {zones.map((z) => (
                <option key={z} value={z} />
              ))}
            </datalist>
            {!validZone(tz) && <p className="field-error">Fuseau inconnu (ex. Europe/Paris, UTC).</p>}
          </div>
          {target !== null && (
            <p className="hint" role="status">
              {target <= now ? (
                <strong>Cette date est déjà passée.</strong>
              ) : (
                <>
                  Reprise le <strong>{fmt(target, tz)}</strong> ({tz}), {relative(target, now)}
                  {tz === "UTC" ? "." : ` — soit ${fmtUtc(target)} UTC.`}
                </>
              )}
            </p>
          )}
          <div className="field">
            <label htmlFor={`${id}-past`}>Si la date est déjà passée</label>
            <select id={`${id}-past`} value={node.on_past ?? "fail"} onChange={(e) => onUpdate({ on_past: e.target.value === "now" ? "now" : "fail" })}>
              <option value="fail">Échouer (branche d'erreur)</option>
              <option value="now">Continuer tout de suite</option>
            </select>
          </div>
        </>
      )}
    </>
  );
}
