import type { EmbeddedFigure, FinalMergeQuestion } from "../api/client";
import { API_BASE } from "../api/client";
import { RichText } from "./RichText";

/**
 * Shared question card — single source of truth for rendering a question
 * across the FinalMerge, Composer, and Preview views. Keeps formatting
 * consistent everywhere (the user explicitly flagged drift between views).
 *
 * Layout (matches the "good" format from screenshot 2):
 *   Q · p.X · type
 *   <question text with inline figures at touchpoints>
 *   <trailing figures with no label-match>
 *   ▼ Solution (open by default)
 *     <solution text with inline figures, KaTeX math, tables>
 */
export function QuestionCard({
  q,
  density = "comfortable",
  defaultSolutionOpen = true,
}: {
  q: FinalMergeQuestion;
  /** "comfortable" for Final / Preview pages (full card with border).
   *  "compact" for Composer rows (no outer card, just the content). */
  density?: "comfortable" | "compact";
  defaultSolutionOpen?: boolean;
}) {
  const figs = q.embedded_figures ?? [];
  const bodyRender = splice(q.raw_text, figs);
  const remainAfterBody = figs.filter(
    (f) => !bodyRender.consumed.has(f.figure_id),
  );
  const solRender =
    q.has_solution && q.solution_text
      ? splice(q.solution_text, remainAfterBody)
      : { nodes: [], consumed: new Set<string>() };
  const trailing = remainAfterBody.filter(
    (f) => !solRender.consumed.has(f.figure_id),
  );

  const wrapper: React.CSSProperties =
    density === "compact"
      ? { margin: "4px 0" }
      : {
          margin: "10px 0",
          padding: "10px 14px",
          border: "1px solid var(--border)",
          borderRadius: 6,
          background: "var(--surface, #fff)",
        };

  // Render a prominent heading above the question card. Preference:
  //   1. exercise_ref — the chip's label like "EXAMPLE 4.3" / "Exercise 8.2"
  //      (set by the backend chip-merge from the chip's `label` field)
  //   2. question_number — bare number like "4.5" if no label exists,
  //      prefixed with "Question " so it reads naturally
  //   3. nothing — practice questions without any identifier stay clean
  const exRef = (q.exercise_ref || "").trim();
  const qNum = (q.question_number || "").trim();
  const heading = exRef ? exRef : qNum ? `Question ${qNum}` : "";
  return (
    <div style={wrapper}>
      {heading && (
        <div
          style={{
            fontSize: "1rem",
            fontWeight: 700,
            color: "var(--text1)",
            marginBottom: 6,
            letterSpacing: 0.2,
          }}
        >
          {heading}
        </div>
      )}
      <div
        style={{
          fontSize: "0.66rem",
          color: "var(--text3)",
          fontFamily: "var(--mono)",
          marginBottom: 4,
        }}
      >
        Q
        {q.question_number ? `${q.question_number}` : ""}
        {q.page_start ? ` · p.${q.page_start}` : ""}
        {q.question_type ? ` · ${q.question_type}` : ""}
      </div>
      {q.image_regen_hint?.needed && (
        <div
          style={{
            margin: "4px 0 8px 0",
            padding: "4px 8px",
            fontSize: "0.66rem",
            background: "rgba(220,53,69,0.08)",
            border: "1px solid rgba(220,53,69,0.25)",
            borderRadius: 4,
            color: "var(--red, #b54552)",
            lineHeight: 1.4,
          }}
          title="LLM flagged that the attached image likely no longer matches this regenerated question. Open the Images tab to regenerate the figure."
        >
          <b>⚠ Figure may need regeneration.</b>
          {q.image_regen_hint.reason && (
            <span style={{ marginLeft: 6, color: "var(--text3)" }}>
              {q.image_regen_hint.reason}
            </span>
          )}
        </div>
      )}
      <div
        style={{
          fontSize: "0.88rem",
          lineHeight: 1.55,
          color: "var(--text1)",
        }}
      >
        {bodyRender.nodes}
      </div>
      {trailing.length > 0 && (
        <div style={{ marginTop: 6 }}>
          {trailing.map((f) => (
            <FigureInline key={`tr-${f.figure_id}`} figure={f} />
          ))}
        </div>
      )}
      {q.has_solution && q.solution_text && (
        <details
          style={{ marginTop: 8 }}
          {...(defaultSolutionOpen ? { open: true } : {})}
        >
          <summary
            style={{
              fontSize: "0.7rem",
              fontWeight: 600,
              color: "var(--text2)",
              cursor: "pointer",
              letterSpacing: 0.3,
            }}
          >
            Solution
          </summary>
          <div
            style={{
              fontSize: "0.82rem",
              lineHeight: 1.5,
              color: "var(--text2)",
              marginTop: 6,
              paddingLeft: 10,
              borderLeft: "3px solid var(--border)",
            }}
          >
            {solRender.nodes}
          </div>
        </details>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Internals — touchpoint splicing (the same regex used in BlockRenderer)
// ---------------------------------------------------------------------------

function splice(
  text: string,
  figures: EmbeddedFigure[],
): { nodes: React.ReactNode[]; consumed: Set<string> } {
  const consumed = new Set<string>();
  if (!text) return { nodes: [], consumed };
  if (figures.length === 0)
    return { nodes: [<RichText key={0} text={text} />], consumed };

  type Hit = { end: number; fig: EmbeddedFigure };
  const hits: Hit[] = [];
  for (const fig of figures) {
    if (!fig.label) continue;
    const num = fig.label.match(/(\d+(?:\.\d+)*)/)?.[1];
    if (!num) continue;
    const escaped = num.replace(/\./g, "\\.");
    const re = new RegExp(
      `(?:Figures?|Figs?\\.?)[\\s._:\\-]*[(\\[]?\\s*${escaped}\\s*[)\\]]?\\b`,
      "i",
    );
    const m = re.exec(text);
    if (m && m.index !== undefined) {
      hits.push({ end: m.index + m[0].length, fig });
    }
  }
  hits.sort((a, b) => a.end - b.end);

  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (const hit of hits) {
    if (consumed.has(hit.fig.figure_id)) continue;
    const chunk = text.slice(cursor, hit.end);
    if (chunk) nodes.push(<RichText key={key++} text={chunk} />);
    nodes.push(
      <FigureInline key={`emb-${hit.fig.figure_id}`} figure={hit.fig} />,
    );
    consumed.add(hit.fig.figure_id);
    cursor = hit.end;
  }
  const tail = text.slice(cursor);
  if (tail) nodes.push(<RichText key={key++} text={tail} />);
  return { nodes, consumed };
}

function FigureInline({ figure }: { figure: EmbeddedFigure }) {
  const src = figure.image_url.startsWith("http")
    ? figure.image_url
    : `${API_BASE}${figure.image_url}`;
  return (
    <figure style={{ margin: "8px 0", textAlign: "center" }}>
      <img
        src={src}
        alt={figure.label || "figure"}
        loading="lazy"
        style={{
          maxWidth: 380,
          height: "auto",
          border: "1px solid var(--border)",
          borderRadius: 4,
        }}
      />
      {(figure.label || figure.caption) && (
        <figcaption
          style={{
            fontSize: "0.7rem",
            color: "var(--text3)",
            fontStyle: "italic",
            marginTop: 2,
          }}
        >
          {figure.label && <b>{figure.label}</b>}
          {figure.label && figure.caption && " — "}
          {figure.caption}
        </figcaption>
      )}
    </figure>
  );
}
