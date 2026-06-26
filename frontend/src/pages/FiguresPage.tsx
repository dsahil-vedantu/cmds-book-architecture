/** Figures pipeline v2 — main page (rev 2).
 *
 * UX mirrors Theory + Questions:
 *   - Top mode toggle: Original | ✨ Regenerated
 *   - Left: tree of the book schema, filtered to nodes that contain
 *     figures (and in Regenerated mode, filtered further to nodes with
 *     ≥1 approved variant). Same hierarchy as theory/questions trees.
 *   - Right: figure cards for the active section
 *   - Per-section regen: inline params panel (no modal) with style /
 *     custom instructions / watermark / overlay / model overrides
 *   - Approve & move to Regenerated workflow stamps approved_at on
 *     every figure with a regen variant in the section
 */

import { useMemo, useState } from "react";
import { useUI } from "../stores/ui";
import {
  useBook,
  useBookFigures,
  useBookFigureRegenerations,
  useBookUnattachedFigures,
  useDeleteFigureReference,
  useExtractFiguresV2,
  useDiscardFigureRegen,
  useHideFigureReference,
  useJob,
  useReembedFigures,
  useRegenerateFiguresSection,
  useApproveSectionFigures,
  useUnapproveSectionFigures,
  useApproveFigure,
  useUnapproveFigure,
} from "../api/hooks";
import {
  api,
  API_BASE,
  type Figure,
  type FigureRegenParams,
  type FigureRegenerationRun,
  type FigureSectionGroup,
  type UnattachedFigure,
  type UUID,
} from "../api/client";

type Mode = "original" | "regenerated";

export function FiguresPage() {
  const { selectedBookId, selectedFigureSectionRef, selectFigureSection } = useUI();
  const { data: book } = useBook(selectedBookId);
  const [activeJobId, setActiveJobId] = useState<UUID | null>(null);
  const { data: liveJob } = useJob(activeJobId, {
    pollMs: activeJobId ? 1500 : undefined,
  });
  const isJobRunning = liveJob?.status === "running" || liveJob?.status === "queued";
  const { data, isLoading, refetch } = useBookFigures(selectedBookId, {
    pollMs: isJobRunning ? 2000 : undefined,
  });
  const extractFigures = useExtractFiguresV2();
  const reembed = useReembedFigures();
  // Regen run history — driven by the new FigureRegenerations endpoint.
  // Polled while a regen job is running so the history updates live.
  const { data: regenRunsData } = useBookFigureRegenerations(selectedBookId, {
    pollMs: isJobRunning ? 2000 : undefined,
  });
  const { data: unattached } = useBookUnattachedFigures(selectedBookId);
  const [mode, setMode] = useState<Mode>("original");
  const [regenOpenForSection, setRegenOpenForSection] = useState<string | null>(null);

  if (!selectedBookId) {
    return (
      <div className="empty" style={{ padding: 40 }}>
        <div className="empty-i">🖼</div>
        <h3>Pick a book from the sidebar</h3>
      </div>
    );
  }

  const total = data?.total_figures ?? 0;
  const allSections = data?.sections ?? [];
  const visibleSections =
    mode === "regenerated"
      ? allSections.filter((s) => s.n_approved > 0)
      : allSections;
  const totalApproved = allSections.reduce((n, s) => n + s.n_approved, 0);
  const activeSection =
    visibleSections.find((s) => s.section_ref === selectedFigureSectionRef)
    ?? visibleSections[0]
    ?? null;

  const onExtract = () => {
    if (!selectedBookId) return;
    if (total > 0) {
      const ok = window.confirm(
        "This book already has extracted figures. Re-running will WIPE all existing figure rows " +
        "(including approved variants) and re-extract from the PDF. Continue?",
      );
      if (!ok) return;
    }
    extractFigures.mutate(
      { bookId: selectedBookId },
      {
        onSuccess: (res) => {
          setActiveJobId(res.job_id);
          void refetch();
        },
      },
    );
  };

  return (
    <div className="cnt">
      <div className="ci" style={{ maxWidth: "none", padding: "16px 22px" }}>
        {/* Header bar */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            marginBottom: 12,
            paddingBottom: 12,
            borderBottom: "1px solid var(--border)",
          }}
        >
          <h2 style={{ margin: 0, fontSize: "1rem", fontWeight: 700 }}>
            {book?.title ?? "—"} · Figures
          </h2>
          <span style={{ fontSize: "0.75rem", color: "var(--text3)" }}>
            {total} extracted · {totalApproved} approved
            {visibleSections.length > 0 && ` · ${visibleSections.length} sections`}
          </span>
          <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
            <button
              className="btn"
              style={{ fontSize: "0.72rem", padding: "4px 12px" }}
              disabled={reembed.isPending || total === 0}
              onClick={() => {
                if (!selectedBookId) return;
                reembed.mutate(selectedBookId);
              }}
              title="Re-run the figure embedder. Deterministic, no Gemini call — just re-matches existing figures to theory/questions."
            >
              {reembed.isPending ? "Re-embedding…" : "↻ Re-embed"}
            </button>
            <button
              className="btn primary"
              style={{ fontSize: "0.72rem", padding: "4px 12px" }}
              disabled={extractFigures.isPending || isJobRunning}
              onClick={onExtract}
              title={
                total > 0
                  ? "Re-extract figures from this book's PDF (wipes existing figures)"
                  : "Extract figures from this book's PDF (1 Gemini call)"
              }
            >
              {extractFigures.isPending || isJobRunning
                ? "Extracting…"
                : total > 0
                  ? "↻ Re-extract figures"
                  : "🖼 Extract figures"}
            </button>
          </div>
        </div>

        {/* Unattached figures — figures the embedder couldn't place anywhere */}
        {(unattached?.figures?.length ?? 0) > 0 && (
          <UnattachedFiguresPanel bookId={selectedBookId} figures={unattached!.figures} />
        )}

        {/* Mode toggle */}
        <div style={{ display: "flex", gap: 4, marginBottom: 14 }}>
          <ModeButton
            active={mode === "original"}
            onClick={() => setMode("original")}
            label="Original"
            count={allSections.length}
          />
          <ModeButton
            active={mode === "regenerated"}
            onClick={() => setMode("regenerated")}
            label="✨ Regenerated"
            count={allSections.filter((s) => s.n_approved > 0).length}
            disabled={totalApproved === 0}
          />
        </div>

        {/* Job progress */}
        {liveJob && isJobRunning && (
          <div
            style={{
              marginBottom: 14,
              padding: "8px 12px",
              background: "var(--bg2, #f5f5fa)",
              borderRadius: 6,
              fontSize: "0.72rem",
            }}
          >
            <div className="prog" style={{ marginBottom: 6 }}>
              <div
                className="progb"
                style={{
                  width: `${Math.max(2, liveJob.progress ?? 0)}%`,
                  transition: "width 0.4s ease",
                }}
              />
            </div>
            <div style={{ color: "var(--text3)" }}>
              {liveJob.message ?? "Working…"}{" "}
              <span style={{ fontFamily: "var(--mono)" }}>{liveJob.progress ?? 0}%</span>
            </div>
          </div>
        )}
        {liveJob?.status === "failed" && liveJob.error && (
          <div
            style={{
              marginBottom: 14,
              padding: "8px 12px",
              background: "rgba(220,53,69,0.08)",
              borderRadius: 6,
              fontSize: "0.72rem",
              color: "var(--red)",
            }}
          >
            ⚠️ Job failed: {liveJob.error}
          </div>
        )}

        {/* Empty state — extraction */}
        {!isLoading && total === 0 && !isJobRunning && (
          <div className="empty" style={{ padding: 40 }}>
            <div className="empty-i">🖼</div>
            <h3>No figures yet</h3>
            <p>
              Click <b>🖼 Extract figures</b> to run the figure pipeline.
              One Gemini call per book — detects every diagram, table, chart,
              and figure with verbatim captions + bounding boxes.
            </p>
          </div>
        )}

        {/* Regen run history — shown in Regenerated mode regardless of
            whether anything is approved. Each run = one click of "Regenerate
            figures" for a section. Mirrors the Questions / Theory regen
            folder concept (separate folder structure per run). */}
        {mode === "regenerated" && (regenRunsData?.runs?.length ?? 0) > 0 && (
          <RegenRunHistoryPanel runs={regenRunsData!.runs} />
        )}

        {/* Empty state — regenerated mode with NO runs at all */}
        {total > 0
          && mode === "regenerated"
          && totalApproved === 0
          && (regenRunsData?.runs?.length ?? 0) === 0 && (
          <div className="empty" style={{ padding: 40 }}>
            <div className="empty-i">✨</div>
            <h3>No regenerated variants yet</h3>
            <p>
              Switch to <b>Original</b>, click <b>🔁 Regenerate figures</b> on a
              section, review the variants, then click <b>✓ Approve & move to
              Regenerated</b> — they'll appear here.
            </p>
          </div>
        )}

        {/* Single-pane content — sidebar's FiguresLens drives navigation.
             Mirrors Theory's ReaderPage / Questions's QuestionsPage pattern
             where the sidebar owns the section tree and the main pane just
             renders content for the active section. */}
        {visibleSections.length > 0 && (
          <div style={{ padding: 4 }}>
            {activeSection ? (
              <FigureSectionPanel
                bookId={selectedBookId}
                sectionRef={activeSection.section_ref}
                figures={activeSection.figures}
                mode={mode}
                isRegenOpen={regenOpenForSection === activeSection.section_ref}
                onOpenRegen={() =>
                  setRegenOpenForSection(activeSection.section_ref)
                }
                onCloseRegen={() => setRegenOpenForSection(null)}
                onRegenStarted={(jobId) => {
                  setRegenOpenForSection(null);
                  setActiveJobId(jobId);
                }}
              />
            ) : (
              <div className="empty" style={{ padding: 40 }}>
                <div className="empty-i">🖼</div>
                <h3>Pick a section from the sidebar</h3>
                <p>
                  The <b>🖼 Images</b> tab in the sidebar lists every section
                  that contains a figure. Click one to see its figure cards.
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Mode toggle button
// ---------------------------------------------------------------------------

function ModeButton({
  active,
  onClick,
  label,
  count,
  disabled,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        flex: "0 0 auto",
        padding: "6px 14px",
        fontSize: "0.74rem",
        fontWeight: 600,
        border: "1px solid var(--border)",
        borderRadius: 6,
        background: active ? "var(--accent)" : "var(--bg2, #f5f5fa)",
        color: active ? "white" : disabled ? "var(--text3)" : "var(--text2)",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {label}
      <span
        style={{
          marginLeft: 6,
          fontSize: "0.66rem",
          opacity: active ? 0.85 : 0.6,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {count}
      </span>
    </button>
  );
}
// Section panel — figure grid + inline regen params + approve workflow
// ---------------------------------------------------------------------------

function FigureSectionPanel({
  bookId,
  sectionRef,
  figures,
  mode,
  isRegenOpen,
  onOpenRegen,
  onCloseRegen,
  onRegenStarted,
}: {
  bookId: UUID;
  sectionRef: string;
  figures: Figure[];
  mode: Mode;
  isRegenOpen: boolean;
  onOpenRegen: () => void;
  onCloseRegen: () => void;
  onRegenStarted: (jobId: UUID) => void;
}) {
  const sortedFigures = useMemo(
    () =>
      [...figures].sort((a, b) => {
        const pa = a.page_number ?? 0;
        const pb = b.page_number ?? 0;
        if (pa !== pb) return pa - pb;
        return (a.normalized_label ?? "").localeCompare(b.normalized_label ?? "");
      }),
    [figures],
  );

  // Visible figures depend on mode — in "regenerated" mode show only approved
  const visibleFigures = useMemo(
    () =>
      mode === "regenerated"
        ? sortedFigures.filter((f) => f.is_approved)
        : sortedFigures,
    [sortedFigures, mode],
  );

  const nWithRegen = figures.filter((f) => f.has_regen).length;
  const nApproved = figures.filter((f) => f.is_approved).length;
  const hasUnapprovedDrafts = nWithRegen > nApproved;

  const approveSection = useApproveSectionFigures();
  const unapproveSection = useUnapproveSectionFigures();

  return (
    <>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginBottom: 14,
          paddingBottom: 10,
          borderBottom: "1px solid var(--border)",
          flexWrap: "wrap",
        }}
      >
        <h3 style={{ margin: 0, fontSize: "0.95rem", fontWeight: 700 }}>{sectionRef}</h3>
        <span style={{ color: "var(--text3)", fontSize: "0.72rem" }}>
          · {visibleFigures.length} figure{visibleFigures.length === 1 ? "" : "s"}
          {nWithRegen > 0 && ` · ${nApproved}/${nWithRegen} approved`}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: 6, flexWrap: "wrap" }}>
          {mode === "original" && (
            <button
              className="btn bg"
              style={{ fontSize: "0.7rem", padding: "4px 10px" }}
              onClick={onOpenRegen}
              disabled={isRegenOpen}
            >
              🔁 Regenerate figures
            </button>
          )}
          {hasUnapprovedDrafts && mode === "original" && (
            <button
              className="btn primary"
              style={{ fontSize: "0.7rem", padding: "4px 10px" }}
              disabled={approveSection.isPending}
              title="Approve every figure with a regen variant in this section"
              onClick={() =>
                approveSection.mutate({ bookId, sectionRef })
              }
            >
              {approveSection.isPending
                ? "Approving…"
                : `✓ Approve & move ${nWithRegen - nApproved} to Regenerated`}
            </button>
          )}
          {mode === "regenerated" && nApproved > 0 && (
            <button
              className="btn bg"
              style={{ fontSize: "0.7rem", padding: "4px 10px", color: "var(--warn, #c80)" }}
              disabled={unapproveSection.isPending}
              title="Move all approved variants back to draft"
              onClick={() => {
                if (!window.confirm("Move all approved variants in this section back to draft?")) return;
                unapproveSection.mutate({ bookId, sectionRef });
              }}
            >
              {unapproveSection.isPending ? "Unapproving…" : "↩ Unapprove section"}
            </button>
          )}
        </div>
      </div>

      {/* Inline params panel — replaces the popup modal */}
      {isRegenOpen && (
        <InlineRegenParams
          bookId={bookId}
          sectionRef={sectionRef}
          onCancel={onCloseRegen}
          onStarted={onRegenStarted}
        />
      )}

      {/* Figure grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(380px, 1fr))",
          gap: 16,
        }}
      >
        {visibleFigures.map((fig) => (
          <FigureCard key={fig.id} fig={fig} bookId={bookId} mode={mode} />
        ))}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Inline regen params panel (replaces the popup modal — full inline flow)
// ---------------------------------------------------------------------------

function InlineRegenParams({
  bookId,
  sectionRef,
  onCancel,
  onStarted,
}: {
  bookId: UUID;
  sectionRef: string;
  onCancel: () => void;
  onStarted: (jobId: UUID) => void;
}) {
  const regen = useRegenerateFiguresSection();
  const [style, setStyle] = useState<"enhanced" | "original">("enhanced");
  const [customInstructions, setCustomInstructions] = useState("");
  const [watermarkClean, setWatermarkClean] = useState(true);
  const [overlay, setOverlay] = useState(true);
  const [imageModel, setImageModel] = useState<string>("");
  const [ocrModel, setOcrModel] = useState<string>("");

  const submit = () => {
    const params: FigureRegenParams = {
      style,
      custom_instructions: customInstructions.trim() || null,
      watermark_clean: watermarkClean,
      overlay,
      image_model: imageModel.trim() || null,
      ocr_model: ocrModel.trim() || null,
    };
    regen.mutate(
      { bookId, sectionRef, params },
      { onSuccess: (res) => onStarted(res.job_id) },
    );
  };

  return (
    <div
      className="card"
      style={{
        background: "var(--bg2, #f5f5fa)",
        border: "1px solid var(--accent)",
        borderRadius: 8,
        padding: 16,
        marginBottom: 16,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 12,
        }}
      >
        <h4 style={{ margin: 0, fontSize: "0.85rem", fontWeight: 700 }}>
          Regenerate figures · {sectionRef}
        </h4>
        <span style={{ fontSize: "0.7rem", color: "var(--text3)" }}>
          Each figure in this section will be regenerated. Latest variant
          replaces any previous regen (no version history).
        </span>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 12,
          marginBottom: 12,
        }}
      >
        <label>
          <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>Style</span>
          <select
            value={style}
            onChange={(e) => setStyle(e.target.value as "enhanced" | "original")}
            style={{
              width: "100%",
              padding: "6px 8px",
              fontSize: "0.76rem",
              marginTop: 4,
              border: "1px solid var(--border)",
              borderRadius: 4,
              background: "var(--bg1)",
              color: "var(--text1)",
            }}
          >
            <option value="enhanced">enhanced — flat-design vector blueprint</option>
            <option value="original">original — 3D aesthetic with gradients</option>
          </select>
        </label>

        <label>
          <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>
            Image gen model
          </span>
          <input
            type="text"
            value={imageModel}
            onChange={(e) => setImageModel(e.target.value)}
            placeholder="gemini-3.1-flash-image-preview (default)"
            style={{
              width: "100%",
              padding: "6px 8px",
              fontSize: "0.76rem",
              marginTop: 4,
              border: "1px solid var(--border)",
              borderRadius: 4,
              background: "var(--bg1)",
              color: "var(--text1)",
            }}
          />
        </label>

        <label style={{ gridColumn: "1 / -1" }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>
            Custom instructions (optional)
          </span>
          <textarea
            value={customInstructions}
            onChange={(e) => setCustomInstructions(e.target.value)}
            placeholder="e.g. Use a more colorful palette · Add more annotations · Simplify"
            rows={3}
            style={{
              width: "100%",
              padding: "6px 8px",
              fontSize: "0.74rem",
              marginTop: 4,
              border: "1px solid var(--border)",
              borderRadius: 4,
              fontFamily: "inherit",
              resize: "vertical",
              background: "var(--bg1)",
              color: "var(--text1)",
            }}
          />
        </label>

        <label>
          <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>
            OCR model (overlay)
          </span>
          <input
            type="text"
            value={ocrModel}
            onChange={(e) => setOcrModel(e.target.value)}
            placeholder="gemini-3.1-pro-preview (default)"
            style={{
              width: "100%",
              padding: "6px 8px",
              fontSize: "0.76rem",
              marginTop: 4,
              border: "1px solid var(--border)",
              borderRadius: 4,
              background: "var(--bg1)",
              color: "var(--text1)",
            }}
          />
        </label>

        <div style={{ display: "flex", flexDirection: "column", gap: 4, paddingTop: 14 }}>
          <label style={{ fontSize: "0.74rem", display: "flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              checked={watermarkClean}
              onChange={(e) => setWatermarkClean(e.target.checked)}
            />
            Remove watermarks (extra Gemini call/figure)
          </label>
          <label style={{ fontSize: "0.74rem", display: "flex", alignItems: "center", gap: 6 }}>
            <input
              type="checkbox"
              checked={overlay}
              onChange={(e) => setOverlay(e.target.checked)}
            />
            Overlay verbatim labels (2 OCR calls/figure)
          </label>
        </div>
      </div>

      {regen.isError && (
        <div style={{ color: "var(--red)", fontSize: "0.72rem", marginBottom: 10 }}>
          {(regen.error as Error).message}
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <button className="btn bg" onClick={onCancel} disabled={regen.isPending}>
          Cancel
        </button>
        <button className="btn primary" onClick={submit} disabled={regen.isPending}>
          {regen.isPending ? "Starting…" : "Start regeneration"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-figure card
// ---------------------------------------------------------------------------

function FigureCard({
  fig,
  bookId,
  mode,
}: {
  fig: Figure;
  bookId: UUID;
  mode: Mode;
}) {
  const discard = useDiscardFigureRegen();
  const approveOne = useApproveFigure();
  const unapproveOne = useUnapproveFigure();
  const [viewing, setViewing] = useState<"both" | "original" | "regenerated">(
    fig.has_regen ? "both" : "original",
  );

  const onDiscard = () => {
    if (!window.confirm("Discard the regenerated variant? The original stays.")) return;
    discard.mutate({ figureId: fig.id, bookId });
  };

  const contexts = (fig.context_hint ?? "")
    .split(",")
    .map((c) => c.trim())
    .filter(Boolean);

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: 8,
        background: "var(--bg1, white)",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          padding: "8px 10px",
          borderBottom: "1px solid var(--border)",
          background: "var(--bg2, #f5f5fa)",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <span style={{ fontWeight: 700, fontSize: "0.78rem" }}>
          {fig.figure_number ?? fig.figure_id_text ?? "Figure"}
        </span>
        {fig.page_number && (
          <span style={{ fontSize: "0.66rem", color: "var(--text3)" }}>
            · p. {fig.page_number}
          </span>
        )}
        {contexts.map((c) =>
          c === "theory" || c === "question" ? <ContextTag key={c} kind={c} /> : null,
        )}
        {fig.is_approved && <ContextTag kind="approved" />}
        <span style={{ marginLeft: "auto", fontSize: "0.62rem", color: "var(--text3)" }}>
          {fig.regen_status === "ready" && `v${fig.regen_version}`}
          {fig.regen_status === "extracting" && "regenerating…"}
          {fig.regen_status === "failed" && "regen failed"}
        </span>
      </div>

      {fig.has_regen && mode === "original" && (
        <div
          style={{
            display: "flex",
            gap: 4,
            padding: "4px 10px",
            background: "var(--bg2, #f5f5fa)",
            borderBottom: "1px solid var(--border)",
          }}
        >
          {(["both", "original", "regenerated"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setViewing(m)}
              style={{
                fontSize: "0.62rem",
                padding: "2px 8px",
                borderRadius: 4,
                border: "1px solid var(--border)",
                background: viewing === m ? "var(--accent)" : "transparent",
                color: viewing === m ? "white" : "var(--text2)",
                cursor: "pointer",
              }}
            >
              {m === "both" ? "Side-by-side" : m === "original" ? "Original" : "Regenerated"}
            </button>
          ))}
        </div>
      )}

      <div
        style={{
          display: "grid",
          gridTemplateColumns:
            mode === "regenerated"
              ? "1fr"
              : viewing === "both" && fig.has_regen
                ? "1fr 1fr"
                : "1fr",
          gap: 4,
          padding: 8,
        }}
      >
        {mode === "regenerated" ? (
          <FigureImage
            label="Regenerated (approved)"
            url={api.figureImageUrl(fig.id, "regenerated")}
            hasImage={fig.has_regen}
          />
        ) : (
          <>
            {(viewing === "original" || viewing === "both" || !fig.has_regen) && (
              <FigureImage
                label="Original"
                url={api.figureImageUrl(fig.id, "original")}
                hasImage={fig.has_original}
              />
            )}
            {fig.has_regen && (viewing === "regenerated" || viewing === "both") && (
              <FigureImage
                label="Regenerated"
                url={api.figureImageUrl(fig.id, "regenerated")}
                hasImage={fig.has_regen}
              />
            )}
          </>
        )}
      </div>

      {fig.caption && (
        <div
          style={{
            padding: "6px 10px",
            borderTop: "1px solid var(--border)",
            fontSize: "0.7rem",
            color: "var(--text2)",
            background: "var(--bg1, white)",
          }}
        >
          {fig.caption}
        </div>
      )}

      <div
        style={{
          padding: "6px 10px",
          borderTop: "1px solid var(--border)",
          display: "flex",
          justifyContent: "flex-end",
          gap: 6,
        }}
      >
        {fig.has_regen && !fig.is_approved && mode === "original" && (
          <button
            className="btn primary"
            style={{ fontSize: "0.66rem", padding: "3px 10px" }}
            disabled={approveOne.isPending}
            onClick={() => approveOne.mutate({ figureId: fig.id, bookId })}
            title="Approve just this figure (keep regenerated, move to Regenerated folder)"
          >
            {approveOne.isPending ? "…" : "✓ Approve"}
          </button>
        )}
        {fig.is_approved && (
          <button
            className="btn bg"
            style={{ fontSize: "0.66rem", padding: "3px 10px", color: "var(--warn, #c80)" }}
            disabled={unapproveOne.isPending}
            onClick={() => unapproveOne.mutate({ figureId: fig.id, bookId })}
            title="Unapprove — drops back to draft"
          >
            {unapproveOne.isPending ? "…" : "↩ Unapprove"}
          </button>
        )}
        {fig.has_regen && (
          <button
            className="btn bg"
            style={{
              fontSize: "0.66rem",
              padding: "3px 10px",
              color: "var(--red, #d33)",
            }}
            disabled={discard.isPending}
            onClick={onDiscard}
            title="Remove the regenerated variant and revert to the original"
          >
            🗑 Discard variant
          </button>
        )}
      </div>
    </div>
  );
}

function FigureImage({
  label,
  url,
  hasImage,
}: {
  label: string;
  url: string;
  hasImage: boolean;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "stretch",
        gap: 4,
      }}
    >
      <div
        style={{
          fontSize: "0.6rem",
          color: "var(--text3)",
          textTransform: "uppercase",
          letterSpacing: 0.5,
          fontWeight: 600,
        }}
      >
        {label}
      </div>
      {hasImage ? (
        <img
          src={url}
          alt={label}
          loading="lazy"
          style={{
            width: "100%",
            height: "auto",
            maxHeight: 320,
            objectFit: "contain",
            border: "1px solid var(--border)",
            borderRadius: 4,
            background: "white",
          }}
        />
      ) : (
        <div
          style={{
            padding: 24,
            border: "1px dashed var(--border)",
            borderRadius: 4,
            color: "var(--text3)",
            fontSize: "0.7rem",
            textAlign: "center",
          }}
        >
          (no image bytes)
        </div>
      )}
    </div>
  );
}

function ContextTag({
  kind,
  count,
  compact,
}: {
  kind: "theory" | "question" | "approved";
  count?: number;
  compact?: boolean;
}) {
  const palette = {
    theory: { bg: "rgba(26,54,110,0.12)", color: "var(--accent)", label: "theory" },
    question: { bg: "rgba(155,89,182,0.12)", color: "#9b59b6", label: "Q" },
    approved: { bg: "rgba(46,204,113,0.14)", color: "#16a085", label: "✓" },
  } as const;
  const styles = palette[kind];
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 2,
        padding: compact ? "0 4px" : "2px 6px",
        borderRadius: 3,
        fontSize: compact ? "0.55rem" : "0.6rem",
        fontWeight: 600,
        background: styles.bg,
        color: styles.color,
        textTransform: "uppercase",
        letterSpacing: 0.3,
      }}
    >
      {styles.label}
      {count !== undefined && count > 1 && <span style={{ opacity: 0.7 }}>×{count}</span>}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Regen run history panel
// ---------------------------------------------------------------------------
// Shows every figure regen attempt grouped into "runs" by the backend (same
// section + close timestamps). Mirrors the Q/Theory regen folder concept.
// Historical run images are NOT recoverable (Figure.regen_image_bytes is
// latest-only) — the list shows metadata + status only.
function RegenRunHistoryPanel({ runs }: { runs: FigureRegenerationRun[] }) {
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: 8,
        marginBottom: 14,
        background: "var(--bg, #fff)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          padding: "8px 12px",
          background: "var(--bg2, #f5f5fa)",
          fontSize: "0.7rem",
          textTransform: "uppercase",
          letterSpacing: 0.4,
          color: "var(--text3)",
          borderBottom: "1px solid var(--border)",
        }}
      >
        ✨ Regen runs · {runs.length}
      </div>
      <div>
        {runs.map((run, idx) => (
          <RegenRunRow key={`${run.section_id}-${run.started_at}-${idx}`} run={run} />
        ))}
      </div>
    </div>
  );
}

function RegenRunRow({ run }: { run: FigureRegenerationRun }) {
  const [expanded, setExpanded] = useState(false);
  const total = run.total;
  const succeeded = run.succeeded;
  const failed = run.failed;
  const allOk = failed === 0 && succeeded === total;
  const style = (run.style_params as { style?: string } | null)?.style;
  const custom =
    (run.style_params as { custom_instructions?: string | null } | null)?.custom_instructions;
  const ts = run.started_at ? new Date(run.started_at) : null;
  const tsLabel = ts
    ? ts.toLocaleString(undefined, {
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";

  return (
    <div style={{ borderBottom: "1px solid var(--b1, #eee)" }}>
      <div
        onClick={() => setExpanded((v) => !v)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "8px 14px",
          cursor: "pointer",
          fontSize: "0.78rem",
        }}
      >
        <span style={{ opacity: 0.7, fontSize: "0.66rem" }}>{expanded ? "▼" : "▶"}</span>
        <span
          style={{
            fontFamily: "var(--mono)",
            color: "var(--text2)",
            fontSize: "0.7rem",
          }}
        >
          §{run.section_id}
        </span>
        <span style={{ color: "var(--text3)", fontSize: "0.7rem" }}>{tsLabel}</span>
        <span
          style={{
            color: allOk ? "var(--green, #2a9d5e)" : "var(--warn, #c80)",
            fontWeight: 600,
            fontSize: "0.7rem",
          }}
        >
          {succeeded}/{total} ok{failed > 0 ? ` · ${failed} failed` : ""}
        </span>
        {style && (
          <span
            style={{
              fontSize: "0.62rem",
              padding: "1px 6px",
              borderRadius: 8,
              background: "var(--bg2, #f5f5fa)",
              color: "var(--text3)",
              textTransform: "uppercase",
              letterSpacing: 0.4,
            }}
          >
            {style}
          </span>
        )}
        {run.model_used && (
          <span
            style={{
              marginLeft: "auto",
              color: "var(--text3)",
              fontSize: "0.66rem",
              fontFamily: "var(--mono)",
            }}
            title="Model used"
          >
            {run.model_used}
          </span>
        )}
      </div>
      {expanded && (
        <div
          style={{
            padding: "8px 14px 12px 36px",
            background: "var(--bg2, #fafafe)",
            fontSize: "0.72rem",
            color: "var(--text2)",
          }}
        >
          {custom && (
            <div style={{ marginBottom: 6 }}>
              <span style={{ color: "var(--text3)" }}>Custom instructions: </span>
              <span style={{ fontStyle: "italic" }}>{custom}</span>
            </div>
          )}
          <div style={{ marginBottom: 6, color: "var(--text3)", fontSize: "0.68rem" }}>
            Per-figure outcomes ({run.rows.length}):
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {run.rows.map((r) => (
              <span
                key={r.id}
                title={`${r.figure_id} — ${r.status}`}
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: "0.64rem",
                  padding: "1px 6px",
                  borderRadius: 4,
                  background:
                    r.status === "ready"
                      ? "rgba(42,157,94,0.12)"
                      : r.status === "failed"
                        ? "rgba(200,128,0,0.12)"
                        : "var(--bg2)",
                  color:
                    r.status === "ready"
                      ? "var(--green, #2a9d5e)"
                      : r.status === "failed"
                        ? "var(--warn, #c80)"
                        : "var(--text3)",
                }}
              >
                {r.status === "ready" ? "✓" : r.status === "failed" ? "✕" : "·"} {r.figure_id.slice(0, 8)}
              </span>
            ))}
          </div>
          <div
            style={{
              marginTop: 8,
              fontSize: "0.66rem",
              color: "var(--text3)",
              fontStyle: "italic",
            }}
          >
            Historical run images are not stored — only the latest regen per figure
            is viewable on the figure card. This list tracks the run metadata.
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// UnattachedFiguresPanel — shows figures the embedder couldn't place anywhere.
// User can click "Restore" to re-include the figure (clears is_hidden and lets
// the embedder retry on next run), or just acknowledge they're aware.
// ---------------------------------------------------------------------------
function UnattachedFiguresPanel({
  bookId,
  figures,
}: {
  bookId: UUID;
  figures: UnattachedFigure[];
}) {
  const hide = useHideFigureReference();
  const del = useDeleteFigureReference();
  const [collapsed, setCollapsed] = useState(false);
  return (
    <div
      style={{
        marginBottom: 14,
        padding: "10px 12px",
        background: "rgba(220,53,69,0.06)",
        border: "1px solid rgba(220,53,69,0.25)",
        borderRadius: 6,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: collapsed ? 0 : 10,
          cursor: "pointer",
        }}
        onClick={() => setCollapsed((c) => !c)}
      >
        <span style={{ fontWeight: 600, fontSize: "0.8rem", color: "var(--red, #d33)" }}>
          ⚠ {figures.length} unattached figure{figures.length === 1 ? "" : "s"}
        </span>
        <span style={{ fontSize: "0.7rem", color: "var(--text3)" }}>
          These figures could not be placed in any section or question. Review and
          decide manually.
        </span>
        <span style={{ marginLeft: "auto", fontSize: "0.7rem", color: "var(--text3)" }}>
          {collapsed ? "▶ show" : "▼ hide"}
        </span>
      </div>
      {!collapsed && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
            gap: 10,
          }}
        >
          {figures.map((f) => {
            const src = f.image_url.startsWith("http")
              ? f.image_url
              : `${API_BASE}${f.image_url}`;
            return (
              <div
                key={f.ref_id || f.figure_id}
                style={{
                  background: "var(--surface, #fff)",
                  border: "1px solid var(--border)",
                  borderRadius: 6,
                  padding: 6,
                  fontSize: "0.7rem",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                    marginBottom: 4,
                  }}
                >
                  {f.label && (
                    <span style={{ fontWeight: 600, color: "var(--text2)" }}>
                      {f.label}
                    </span>
                  )}
                  <span
                    style={{
                      fontSize: "0.6rem",
                      padding: "1px 4px",
                      borderRadius: 4,
                      background:
                        f.context === "question"
                          ? "rgba(91,108,255,0.12)"
                          : "rgba(255,165,0,0.12)",
                      color:
                        f.context === "question"
                          ? "var(--accent, #5b6cff)"
                          : "var(--warn, #c80)",
                    }}
                  >
                    {f.context}
                  </span>
                  {f.ref_id && (
                    <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
                      <button
                        type="button"
                        onClick={() =>
                          hide.mutate({ refId: f.ref_id, bookId })
                        }
                        disabled={hide.isPending || del.isPending}
                        title="Mark as resolved — dismiss from this panel. Re-embed will reintroduce if it still matches."
                        style={{
                          background: "transparent",
                          border: "1px solid var(--border)",
                          borderRadius: 4,
                          color: "var(--text3)",
                          fontSize: "0.62rem",
                          padding: "1px 6px",
                          cursor: hide.isPending ? "default" : "pointer",
                        }}
                      >
                        ✓ Resolved
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (
                            !window.confirm(
                              `Delete this figure placement permanently? The image itself stays, but this reference will be removed. Re-embed will recreate it only if the figure still matches.`,
                            )
                          )
                            return;
                          del.mutate({ refId: f.ref_id, bookId });
                        }}
                        disabled={hide.isPending || del.isPending}
                        title="Hard-delete this figure_reference row"
                        style={{
                          background: "transparent",
                          border: "1px solid rgba(220,53,69,0.4)",
                          borderRadius: 4,
                          color: "var(--red, #d33)",
                          fontSize: "0.62rem",
                          padding: "1px 6px",
                          cursor: del.isPending ? "default" : "pointer",
                        }}
                      >
                        🗑 Delete
                      </button>
                    </div>
                  )}
                </div>
                <img
                  src={src}
                  alt={f.label || f.caption || "figure"}
                  loading="lazy"
                  style={{
                    maxWidth: "100%",
                    height: "auto",
                    display: "block",
                    borderRadius: 4,
                    border: "1px solid var(--border)",
                  }}
                />
                <div style={{ marginTop: 4, color: "var(--text3)" }}>
                  {f.section_ref && <span>§ {f.section_ref}</span>}
                  {f.page_number != null && (
                    <span style={{ marginLeft: 6 }}>p.{f.page_number}</span>
                  )}
                </div>
                {f.caption && (
                  <div
                    style={{
                      marginTop: 4,
                      fontStyle: "italic",
                      color: "var(--text3)",
                      maxHeight: 40,
                      overflow: "hidden",
                    }}
                  >
                    {f.caption}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
