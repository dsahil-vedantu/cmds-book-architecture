import type { Block, EmbeddedFigure, Section } from "../api/client";
import { API_BASE } from "../api/client";
import { useHideFigureReference, useSections } from "../api/hooks";
import { useUI } from "../stores/ui";
import { RichText } from "./RichText";

// Strip leading "1. ", "2) ", "(3) " etc. so we don't double up the marker
// when the <ol> auto-numbers the item. Also handles bare dashes and bullets.
function stripLeadingNumber(text: string): string {
  return text.replace(/^\s*(?:\(\s*\d+\s*\)|\d+[.)])\s+/, "").replace(/^\s*[-•]\s+/, "");
}

// Splice EmbeddedFigure components at the touchpoint where "Figure X.Y"
// appears in text. Returns rendered nodes plus the set of figure_ids that
// were placed inline (so the caller can render the remainder after the
// block).
function spliceFiguresInline(
  text: string,
  figures: EmbeddedFigure[],
): { nodes: (string | JSX.Element)[]; consumedIds: Set<string> } {
  const consumedIds = new Set<string>();
  if (!text || figures.length === 0) {
    return { nodes: [text], consumedIds };
  }
  type Hit = { end: number; fig: EmbeddedFigure };
  const hits: Hit[] = [];
  for (const fig of figures) {
    if (!fig.label) continue;
    const num = fig.label.match(/(\d+(?:\.\d+)*)/)?.[1];
    if (!num) continue;
    const escaped = num.replace(/\./g, "\\.");
    // Accept "Figure 4.2", "Fig 4.2", "Fig. 4.2", "Fig_4.2", "Fig:4.2",
    // "Figure4.2", "Figures 4.2", "Figs 4.2", etc. — tolerant of OCR.
    const re = new RegExp(`(?:Figures?|Figs?\\.?)[\\s._:\\-]*[(\\[]?\\s*${escaped}\\s*[)\\]]?\\b`, "i");
    const m = re.exec(text);
    if (m && m.index !== undefined) {
      hits.push({ end: m.index + m[0].length, fig });
    }
  }
  hits.sort((a, b) => a.end - b.end);

  const nodes: (string | JSX.Element)[] = [];
  let cursor = 0;
  for (const hit of hits) {
    if (consumedIds.has(hit.fig.figure_id)) continue;
    nodes.push(text.slice(cursor, hit.end));
    nodes.push(
      <FigureBlock key={`emb-${hit.fig.figure_id}`} figure={hit.fig} />,
    );
    consumedIds.add(hit.fig.figure_id);
    cursor = hit.end;
  }
  nodes.push(text.slice(cursor));
  return { nodes, consumedIds };
}

export function BlockRenderer({
  blocks,
  embeddedFigures,
}: {
  blocks: Block[];
  /** Optional. When provided, inline figures render after the
   *  block at their `placement_block_idx`; appended / needs_review
   *  figures render at the end with a small "review" badge. */
  embeddedFigures?: EmbeddedFigure[];
}) {
  // Group figures by placement_block_idx for inline rendering; collect
  // appended / needs_review ones to render at the bottom.
  const inlineByIdx: Record<number, EmbeddedFigure[]> = {};
  const trailing: EmbeddedFigure[] = [];
  for (const f of embeddedFigures ?? []) {
    if (
      f.placement_kind === "inline" &&
      f.placement_block_idx !== null &&
      f.placement_block_idx !== undefined
    ) {
      (inlineByIdx[f.placement_block_idx] ||= []).push(f);
    } else {
      trailing.push(f);
    }
  }

  return (
    <div>
      {blocks.map((b, i) => {
        const figs = inlineByIdx[i] ?? [];
        // Try to splice figures at the touchpoint inside the block's text.
        // Only paragraph-like blocks have free-flowing text where this
        // works cleanly; for headers/equations/lists/tables we leave the
        // figure as a "below the block" placement.
        let inlineNodes: (string | JSX.Element)[] | null = null;
        let leftoverFigs: EmbeddedFigure[] = figs;
        if (figs.length > 0) {
          const splicableText =
            b.t === "p"
              ? b.c
              : b.t === "def"
                ? b.c
                : b.t === "kp"
                  ? b.c
                  : null;
          if (splicableText) {
            const { nodes, consumedIds } = spliceFiguresInline(splicableText, figs);
            if (consumedIds.size > 0) {
              inlineNodes = nodes;
              leftoverFigs = figs.filter((f) => !consumedIds.has(f.figure_id));
            }
          }
        }
        return (
          <div key={i}>
            {inlineNodes ? (
              <BlockView block={b} inlineNodes={inlineNodes} />
            ) : (
              <BlockView block={b} />
            )}
            {leftoverFigs.map((f) => (
              <FigureBlock key={f.figure_id} figure={f} />
            ))}
          </div>
        );
      })}
      {trailing.length > 0 && (
        <div style={{ marginTop: 12 }}>
          {trailing.map((f) => (
            <FigureBlock key={f.figure_id} figure={f} appended />
          ))}
        </div>
      )}
    </div>
  );
}

function FigureBlock({
  figure,
  appended,
}: {
  figure: EmbeddedFigure;
  appended?: boolean;
}) {
  // image_url from the backend is an absolute path (e.g.
  // "/api/figures/xyz/image?variant=auto"). Prepend API_BASE so it points
  // at the right backend host in dev + prod.
  const src = figure.image_url.startsWith("http")
    ? figure.image_url
    : `${API_BASE}${figure.image_url}`;
  const isRegen = figure.variant === "regen";
  const isReview = figure.placement_kind === "needs_review";
  const { selectedBookId } = useUI();
  const hide = useHideFigureReference();
  const onRemove = () => {
    if (!figure.ref_id || !selectedBookId) return;
    if (
      !window.confirm(
        `Remove ${figure.label || "this figure"} from this location? It won't appear here or in exports. You can restore it from the Figures page.`,
      )
    )
      return;
    hide.mutate({ refId: figure.ref_id, bookId: selectedBookId });
  };
  return (
    <div
      className="blk"
      style={{
        marginTop: 8,
        marginBottom: 8,
        border: appended ? "1px dashed var(--border)" : undefined,
        padding: appended ? 8 : undefined,
        borderRadius: appended ? 6 : undefined,
        background: appended ? "var(--bg2, #fafbfd)" : undefined,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: "0.72rem",
          color: "var(--text3)",
          marginBottom: 4,
        }}
      >
        {figure.label && (
          <span style={{ fontWeight: 600, color: "var(--text2)" }}>
            {figure.label}
          </span>
        )}
        {isRegen && (
          <span
            style={{
              fontSize: "0.62rem",
              fontWeight: 600,
              padding: "1px 5px",
              borderRadius: 6,
              background: "rgba(91,108,255,0.15)",
              color: "var(--accent, #5b6cff)",
            }}
            title="Regenerated variant (approved)"
          >
            ✨ Regenerated
          </span>
        )}
        {appended && (
          <span
            style={{
              fontSize: "0.62rem",
              fontWeight: 600,
              padding: "1px 5px",
              borderRadius: 6,
              background: isReview
                ? "rgba(220,53,69,0.12)"
                : "rgba(255,165,0,0.15)",
              color: isReview ? "var(--red, #d33)" : "var(--warn, #c80)",
            }}
            title={
              isReview
                ? "No theory body to attach to — please verify placement"
                : "No label match found in this section — appended at end for review"
            }
          >
            ⚠ {isReview ? "Needs review" : "Auto-appended"}
          </span>
        )}
        <button
          type="button"
          onClick={onRemove}
          disabled={hide.isPending || !figure.ref_id}
          title="Remove this figure from this location (excluded from export)"
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: "1px solid var(--border)",
            borderRadius: 4,
            color: "var(--text3)",
            cursor: hide.isPending ? "default" : "pointer",
            fontSize: "0.7rem",
            padding: "1px 6px",
            lineHeight: 1,
          }}
        >
          ✕
        </button>
      </div>
      <img
        src={src}
        alt={figure.label || figure.caption || "figure"}
        loading="lazy"
        style={{
          maxWidth: "100%",
          height: "auto",
          display: "block",
          borderRadius: 4,
          border: "1px solid var(--border)",
        }}
      />
      {figure.caption && (
        <div
          style={{
            fontSize: "0.72rem",
            color: "var(--text3)",
            fontStyle: "italic",
            marginTop: 4,
          }}
        >
          {figure.caption}
        </div>
      )}
    </div>
  );
}

function BlockView({
  block,
  inlineNodes,
}: {
  block: Block;
  /** Optional. When provided, replaces the block's primary text content —
   *  callers pass pre-spliced nodes that mix text with inline <FigureBlock>
   *  components at touchpoints. Only meaningful for paragraph-like blocks. */
  inlineNodes?: (string | JSX.Element)[];
}) {
  switch (block.t) {
    case "p":
      return (
        <div className="blk">
          <p className="blkp">
            {inlineNodes ?? <RichText text={block.c} />}
          </p>
        </div>
      );
    case "h3":
      return (
        <div className="blk">
          <h3 className="blkh3">
            <RichText text={block.c} display="inline" />
          </h3>
        </div>
      );
    case "eq":
      return (
        <div className="blk">
          <div className="blkeq">
            <RichText text={block.c} />
          </div>
        </div>
      );
    case "def":
      return (
        <div className="blk">
          <div className="blkdef">
            <div className="dl">Definition</div>
            <div className="dt">
              <RichText text={block.term} display="inline" />
            </div>
            <div className="dd">
              {inlineNodes ?? <RichText text={block.c} />}
            </div>
          </div>
        </div>
      );
    case "kp":
      return (
        <div className="blk">
          <div className="blkkp">
            <div className="kl">Key Point</div>
            <div className="kb">
              {inlineNodes ?? <RichText text={block.c} />}
            </div>
          </div>
        </div>
      );
    case "fig":
      return (
        <div className="blk">
          <div className="blkfig">
            {block.label && <span style={{ fontWeight: 600, marginRight: 6 }}>{block.label}</span>}
            {block.c}
          </div>
        </div>
      );
    case "example_ref":
    case "exercise_ref":
    case "question_ref":
      return <RefChip block={block} />;
    case "list":
      return (
        <div className="blk">
          <ol className="blkul">
            {block.items.map((it, i) => (
              <li key={i}>
                <RichText text={stripLeadingNumber(it)} display="inline" />
              </li>
            ))}
          </ol>
        </div>
      );
    case "table":
      return (
        <div className="blk">
          {block.caption && <div style={{ fontSize: "0.78rem", color: "var(--text3)", marginBottom: 6, fontStyle: "italic" }}>{block.caption}</div>}
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.82rem" }}>
            {block.headers?.length > 0 && (
              <thead>
                <tr>
                  {block.headers.map((h: string, i: number) => (
                    <th key={i} style={{ border: "1px solid var(--border)", padding: "6px 10px", background: "var(--surface2)", textAlign: "left", fontWeight: 600 }}>
                      <RichText text={h} display="inline" />
                    </th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {block.rows?.map((row: string[], ri: number) => (
                <tr key={ri}>
                  {row.map((cell: string, ci: number) => (
                    <td key={ci} style={{ border: "1px solid var(--border)", padding: "6px 10px", verticalAlign: "top" }}>
                      <RichText text={cell} display="inline" />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "example":
      return (
        <div className="blk">
          <div className="blkex">
            <div className="exh">
              <div className="exl">{block.label || "Example"}</div>
              {block.prob && (
                <div className="exp">
                  <RichText text={block.prob} />
                </div>
              )}
            </div>
            {block.eqs.length > 0 && (
              <div className="exb">
                {block.eqs.map((e, i) => (
                  <div key={i} className="exeq">
                    <RichText text={e} />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      );
    default:
      return null;
  }
}

/** Clickable touchpoint chip injected by example_linker. Clicking navigates
 *  the Reader to the example's own theory section (which holds the worked
 *  problem + solution). */
function RefChip({
  block,
}: {
  block: Extract<Block, { t: "example_ref" | "exercise_ref" | "question_ref" }>;
}) {
  const tone =
    block.t === "example_ref"
      ? { bg: "#eef6ff", fg: "#1d4ed8", kind: "Worked example" }
      : block.t === "exercise_ref"
        ? { bg: "#fef3c7", fg: "#92400e", kind: "Exercise" }
        : { bg: "#ecfdf5", fg: "#047857", kind: "Question" };

  const { selectedBookId, selectSection, setView } = useUI();
  const { data: sections } = useSections(selectedBookId);

  const targetSectionId = block.section_id;
  const target = targetSectionId
    ? (sections ?? []).find((s: Section) => s.section_id === targetSectionId)
    : undefined;

  const clickable = !!target;
  return (
    <div className="blk">
      <button
        type="button"
        disabled={!clickable}
        onClick={() => {
          if (target) {
            selectSection(target.id);
            setView("reader");
          }
        }}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          padding: "4px 10px",
          borderRadius: 999,
          fontSize: "0.78rem",
          background: tone.bg,
          color: tone.fg,
          border: "1px solid rgba(0,0,0,0.08)",
          cursor: clickable ? "pointer" : "default",
          font: "inherit",
        }}
        title={
          clickable
            ? `Open ${block.label || tone.kind} — has full problem + solution`
            : `${tone.kind} (no linked content yet)`
        }
      >
        <span style={{ fontWeight: 600 }}>{tone.kind}</span>
        <span>{block.label || block.number || ""}</span>
        {clickable && <span style={{ opacity: 0.6 }}>→</span>}
      </button>
    </div>
  );
}
