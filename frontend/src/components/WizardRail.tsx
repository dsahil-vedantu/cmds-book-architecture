import { useUI, type View } from "../stores/ui";

export type RailStep = "upload" | "analyse" | "schema" | "extract" | "done";

const ORDER: RailStep[] = ["upload", "analyse", "schema", "extract", "done"];
const LABELS: Record<RailStep, string> = {
  upload: "Upload",
  analyse: "Analyse",
  schema: "Schema",
  extract: "Extract",
  done: "Save",
};

// Map rail step → app view (clicking a step routes to that view).
const STEP_TO_VIEW: Record<RailStep, View> = {
  upload: "upload",
  analyse: "schema", // analysis lives on the schema page
  schema: "schema",
  extract: "reader",
  done: "reader",
};

export function WizardRail({ active }: { active: RailStep }) {
  const activeIdx = ORDER.indexOf(active);
  const setView = useUI((s) => s.setView);
  return (
    <div className="rail">
      {ORDER.map((step, i) => {
        const cls =
          i < activeIdx ? "rs done" : i === activeIdx ? "rs active" : "rs";
        return (
          <button
            key={step}
            type="button"
            className={cls}
            onClick={() => setView(STEP_TO_VIEW[step])}
            style={{ border: "none", background: "transparent", cursor: "pointer" }}
            title={`Go to ${LABELS[step]}`}
          >
            <div className="rsd" />
            {LABELS[step]}
          </button>
        );
      })}
    </div>
  );
}
