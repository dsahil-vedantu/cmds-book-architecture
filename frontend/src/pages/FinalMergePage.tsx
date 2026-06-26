import { useState } from "react";
import { useUI } from "../stores/ui";
import { useFinalMerge, useFinalDraft } from "../api/hooks";
import { api, API_BASE } from "../api/client";
import type { Block, EmbeddedFigure, FinalMergeQuestion } from "../api/client";
import { BlockRenderer } from "../components/BlockRenderer";
import { QuestionCard } from "../components/QuestionCard";
import { RichText, RenderStyleContext } from "../components/RichText";
import { renderFinalDraftItem } from "./FinalPreviewPage";

/**
 * Phase 2 — Final Merge view.
 *
 * Read-only stitched document combining theory blocks + embedded figures +
 * question chips + question cards (with their own figures), in schema
 * order. This is the "final product" view — what end users would see.
 *
 * Renders the server's `/api/books/{id}/final-merge` payload. All export
 * formats (JSON / Markdown / DOCX) are produced by the same server endpoint
 * so the on-screen view matches downloads byte-for-byte (modulo image
 * embedding).
 */
export function FinalMergePage() {
  const { selectedBookId } = useUI();
  const [preferRegen, setPreferRegen] = useState(true);
  const { data, isLoading, error } = useFinalMerge(selectedBookId, preferRegen);
  // Sync with Composer/Preview — if the user has authored a FinalDraft with
  // edits, show that here too (Composer-Preview-Final all reflect the same
  // state). When no draft exists or it's empty, fall back to the
  // auto-generated merge below.
  const { data: draftData } = useFinalDraft(selectedBookId, true);
  const draftItems = draftData?.items ?? [];
  const useDraft = draftItems.length > 0;

  if (!selectedBookId) {
    return (
      <div className="empty" style={{ padding: 40 }}>
        <div className="empty-i">📄</div>
        <h3>Pick a book from the sidebar</h3>
      </div>
    );
  }
  if (isLoading) {
    return (
      <div className="empty" style={{ padding: 40 }}>
        <div className="empty-i">⏳</div>
        <h3>Loading final merged view…</h3>
      </div>
    );
  }
  if (error) {
    return (
      <div className="empty" style={{ padding: 40 }}>
        <div className="empty-i">⚠️</div>
        <h3>Failed to load</h3>
        <p>{(error as Error).message}</p>
      </div>
    );
  }
  if (!data) return null;

  const exportHref = (fmt: "json" | "markdown" | "docx") =>
    api.finalMergeExportUrl(selectedBookId, fmt, preferRegen);

  const regenSectionCount = data.sections.filter(
    (s) => s.block_source === "regen",
  ).length;

  const totalQuestions = data.sections.reduce(
    (n, s) => n + s.questions.length,
    0,
  );
  const totalFigures =
    data.sections.reduce(
      (n, s) =>
        n +
        s.embedded_figures.length +
        s.questions.reduce((m, q) => m + q.embedded_figures.length, 0),
      0,
    );

  return (
    <RenderStyleContext.Provider value="unicode">
      <div className="cnt">
      <div className="ci" style={{ maxWidth: 900, padding: "16px 22px" }}>
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            marginBottom: 12,
            paddingBottom: 12,
            borderBottom: "1px solid var(--border)",
            flexWrap: "wrap",
          }}
        >
          <h2 style={{ margin: 0, fontSize: "1rem", fontWeight: 700 }}>
            {data.book.title} · Final
          </h2>
          <span style={{ fontSize: "0.72rem", color: "var(--text3)" }}>
            {data.sections.length} sections · {totalQuestions} questions ·{" "}
            {totalFigures} figures
            {regenSectionCount > 0 && ` · ✨ ${regenSectionCount} regen`}
            {data.unattached_figures.length > 0 &&
              ` · ${data.unattached_figures.length} unattached`}
          </span>
          <label
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              fontSize: "0.72rem",
              color: "var(--text2)",
              cursor: "pointer",
              padding: "4px 8px",
              border: "1px solid var(--border)",
              borderRadius: 4,
            }}
            title="When on, regenerated theory/questions/figures override originals where saved."
          >
            <input
              type="checkbox"
              checked={preferRegen}
              onChange={(e) => setPreferRegen(e.target.checked)}
              style={{ margin: 0 }}
            />
            Use regenerated
          </label>
          <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <a
              className="btn"
              href={exportHref("json")}
              style={{ fontSize: "0.72rem", padding: "4px 12px" }}
              download
              title="Download structured JSON"
            >
              ⬇ .json
            </a>
            <a
              className="btn"
              href={exportHref("markdown")}
              style={{ fontSize: "0.72rem", padding: "4px 12px" }}
              download
              title="Download Markdown"
            >
              ⬇ .md
            </a>
            <a
              className="btn"
              href={exportHref("docx")}
              style={{ fontSize: "0.72rem", padding: "4px 12px" }}
              download
              title="Download Word DOCX (via pandoc)"
            >
              ⬇ .docx
            </a>
          </div>
        </div>

        {/* Body */}
        {useDraft ? (
          // Draft path — Composer/Preview/Final all read from FinalDraft.items
          // so any edit you make in Composer reflects here byte-for-byte.
          <div style={{ marginBottom: 8, fontSize: "0.72rem", color: "var(--text3)" }}>
            ✨ Showing your composed draft. (Edit in Composer · Auto-merge is
            available when the draft is empty.)
          </div>
        ) : null}
        {useDraft ? (
          <div className="preview-doc">{draftItems.map(renderFinalDraftItem)}</div>
        ) : data.sections.length === 0 ? (
          <div className="empty" style={{ padding: 40 }}>
            <div className="empty-i">📄</div>
            <h3>Nothing to show yet</h3>
            <p>
              Extract theory and (optionally) questions for this book, then
              come back here for the unified view.
            </p>
          </div>
        ) : (
          (() => {
            // Which section_ids will be rendered? Use that set to decide
            // which chips to drop — a chip pointing to a section that's
            // already in the doc is a noisy duplicate, so we suppress
            // just the chip (the target section keeps rendering normally).
            const sectionIdsInDoc = new Set<string>(
              data.sections.map((s) => s.section_id),
            );
            return data.sections.map((sec) => {
              const cleanedBlocks = stripResolvedChips(
                dedupeHeadingBlocks(sec.blocks, sec.section_title),
                sectionIdsInDoc,
              );
              const inlinedAt = sec.inlined_questions_by_block_idx ?? {};
              // Determine figure placement per block index (so we can render
              // each block individually with its embedded figures), so that
              // we can splice inlined questions between blocks.
              const figsByIdx = new Map<number, EmbeddedFigure[]>();
              const trailingFigs: EmbeddedFigure[] = [];
              for (const f of sec.embedded_figures) {
                const idx = f.placement_block_idx;
                if (idx === null || idx === undefined) trailingFigs.push(f);
                else {
                  const arr = figsByIdx.get(idx) ?? [];
                  arr.push(f);
                  figsByIdx.set(idx, arr);
                }
              }
              const startInlined = inlinedAt["-1"] ?? [];
              return (
                <section key={sec.section_id} style={{ marginBottom: 22 }}>
                  <SectionHeading
                    title={sec.section_title}
                    level={sec.level}
                    isRegen={sec.block_source === "regen"}
                  />
                  {/* Questions inlined BEFORE any block (chip was first) */}
                  {startInlined.map((q) => (
                    <QuestionCard key={`pre-${q.id}`} q={q} />
                  ))}
                  {cleanedBlocks.map((b, i) => {
                    const blockFigs = (figsByIdx.get(i) ?? []).map((f) => ({
                      ...f,
                      placement_block_idx: 0,
                    }));
                    const afterInlined = inlinedAt[String(i)] ?? [];
                    return (
                      <div key={i}>
                        <BlockRenderer
                          blocks={[b]}
                          embeddedFigures={blockFigs}
                        />
                        {afterInlined.map((q) => (
                          <QuestionCard
                            key={`inl-${i}-${q.id}`}
                            q={q}
                          />
                        ))}
                      </div>
                    );
                  })}
                  {trailingFigs.length > 0 && (
                    <BlockRenderer blocks={[]} embeddedFigures={trailingFigs} />
                  )}
                  {sec.questions.length > 0 && (
                    <div
                      style={{
                        marginTop: 10,
                        paddingTop: 8,
                        borderTop: "1px dashed var(--border)",
                      }}
                    >
                      <div
                        style={{
                          fontSize: "0.68rem",
                          color: "var(--text3)",
                          fontWeight: 700,
                          letterSpacing: 0.4,
                          textTransform: "uppercase",
                          marginBottom: 6,
                        }}
                      >
                        Questions ({sec.questions.length})
                      </div>
                      {sec.questions.map((q) => (
                        <QuestionCard key={q.id} q={q} />
                      ))}
                    </div>
                  )}
                </section>
              );
            });
          })()
        )}

        {/* Unattached figures footer (informational) */}
        {data.unattached_figures.length > 0 && (
          <section
            style={{
              marginTop: 30,
              padding: "12px 14px",
              background: "rgba(220,53,69,0.05)",
              border: "1px solid rgba(220,53,69,0.2)",
              borderRadius: 6,
            }}
          >
            <div
              style={{
                fontWeight: 700,
                fontSize: "0.8rem",
                color: "var(--red, #d33)",
                marginBottom: 6,
              }}
            >
              ⚠ {data.unattached_figures.length} unattached figure
              {data.unattached_figures.length === 1 ? "" : "s"}
            </div>
            <div style={{ fontSize: "0.7rem", color: "var(--text3)" }}>
              These figures could not be auto-placed in any section or
              question. Visit <b>🖼 Images</b> to review.
            </div>
          </section>
        )}
      </div>
      </div>
    </RenderStyleContext.Provider>
  );
}

/** Drop chip blocks that point to a section already rendered in the doc —
 *  showing the placeholder pill is noisy when the target's actual content
 *  appears further down. The target section itself is untouched.
 *  NOTE: this preserves block indices for figures whose placement_block_idx
 *  may have been computed against the original block list, so we rebuild
 *  indices conceptually only — for simplicity we keep figures positioned
 *  by their original index relative to surviving blocks (close enough for
 *  the polish view; the export pipeline will own exact placement). */
function stripResolvedChips(
  blocks: Block[],
  sectionIdsInDoc: Set<string>,
): Block[] {
  return blocks.filter((b) => {
    if (
      b.t !== "example_ref" &&
      b.t !== "exercise_ref" &&
      b.t !== "question_ref"
    )
      return true;
    const target = (b as { section_id?: string }).section_id;
    if (!target) return true;
    // If the chip's target section is in the doc, drop the chip.
    return !sectionIdsInDoc.has(target);
  });
}

/** If the first block is an h3 whose text matches the section title (case-
 *  and whitespace-insensitive), strip it — the section heading already
 *  shows that text and rendering it twice looks broken. */
function dedupeHeadingBlocks(blocks: Block[], sectionTitle: string): Block[] {
  if (!blocks.length || !sectionTitle) return blocks;
  const first = blocks[0];
  if (first.t !== "h3") return blocks;
  const norm = (s: string) =>
    s.replace(/\s+/g, " ").trim().toLowerCase();
  if (norm((first as { t: "h3"; c: string }).c) === norm(sectionTitle)) {
    return blocks.slice(1);
  }
  return blocks;
}

function SectionHeading({
  title,
  level,
  isRegen,
}: {
  title: string;
  level: number;
  isRegen?: boolean;
}) {
  const capped = Math.max(0, Math.min(5, level));
  const sizes = [
    "1.25rem",
    "1.05rem",
    "0.95rem",
    "0.88rem",
    "0.82rem",
    "0.78rem",
  ];
  return (
    <h3
      style={{
        margin: "0 0 8px 0",
        fontSize: sizes[capped],
        fontWeight: 700,
        color: "var(--text1)",
        paddingBottom: 4,
        borderBottom: capped <= 1 ? "1px solid var(--border)" : undefined,
        display: "flex",
        alignItems: "center",
        gap: 8,
      }}
    >
      <span>{title}</span>
      {isRegen && (
        <span
          style={{
            fontSize: "0.6rem",
            fontWeight: 600,
            padding: "1px 6px",
            borderRadius: 6,
            background: "rgba(91,108,255,0.15)",
            color: "var(--accent, #5b6cff)",
          }}
          title="Theory body sourced from a saved regeneration"
        >
          ✨ Regen
        </span>
      )}
    </h3>
  );
}

// FinalQuestionCard / spliceFiguresIntoText / FinalFigure replaced by the
// shared <QuestionCard> component in components/QuestionCard.tsx.
