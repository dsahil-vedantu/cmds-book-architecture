import { useFinalDraft } from "../api/hooks";
import { useUI } from "../stores/ui";
import { api, API_BASE } from "../api/client";
import type { FinalDraftItem } from "../api/client";
import { BlockRenderer } from "../components/BlockRenderer";
import { QuestionCard } from "../components/QuestionCard";
import { RichText, RenderStyleContext } from "../components/RichText";

/**
 * Phase 3.4 — Draft-aware Preview.
 *
 * Renders the FinalDraft items list exactly as they will appear in the
 * exported DOCX/MD/JSON — read-only, single stacked document, no edit
 * chrome, no drag handles. This is the "what you'll get" view that
 * mirrors the Composer's authored state byte-for-byte.
 *
 * Different from the Final tab: that one shows the auto-generated
 * Final Merge (pre-edit). This one shows YOUR composition after edits.
 */
export function FinalPreviewPage() {
  const { selectedBookId, setView } = useUI();
  const { data, isLoading, error } = useFinalDraft(selectedBookId, true);

  if (!selectedBookId) {
    return (
      <div className="empty" style={{ padding: 40 }}>
        <div className="empty-i">👁</div>
        <h3>Pick a book from the sidebar</h3>
      </div>
    );
  }
  if (isLoading) {
    return (
      <div className="empty" style={{ padding: 40 }}>
        <div className="empty-i">⏳</div>
        <h3>Loading preview…</h3>
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="empty" style={{ padding: 40 }}>
        <div className="empty-i">⚠️</div>
        <h3>Failed to load draft</h3>
        <p>{(error as Error)?.message}</p>
      </div>
    );
  }

  const items = data.items ?? [];
  const exportHref = (fmt: "json" | "markdown" | "docx") =>
    api.finalDraftExportUrl(selectedBookId, fmt);

  return (
    <RenderStyleContext.Provider value="unicode">
      <div className="cnt">
      <div className="ci" style={{ maxWidth: 860, padding: "16px 22px" }}>
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            marginBottom: 16,
            paddingBottom: 12,
            borderBottom: "1px solid var(--border)",
            flexWrap: "wrap",
          }}
        >
          <h2 style={{ margin: 0, fontSize: "1rem", fontWeight: 700 }}>
            👁 Preview
          </h2>
          <span style={{ fontSize: "0.72rem", color: "var(--text3)" }}>
            {items.length} items · reflects current Composer draft
          </span>
          <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <button
              className="btn"
              style={{ fontSize: "0.72rem", padding: "4px 12px" }}
              onClick={() => setView("compose")}
              title="Back to Composer to edit"
            >
              ← Edit in Composer
            </button>
            <a
              className="btn primary"
              href={exportHref("docx")}
              download
              style={{ fontSize: "0.72rem", padding: "4px 12px" }}
              title="Download Word DOCX"
            >
              ⬇ .docx
            </a>
          </div>
        </div>

        {/* Body — render each item type. Adjacent items of compatible
            types (e.g. paragraphs, equations, lists) flow naturally; the
            BlockRenderer handles its own block-level styling. */}
        {items.length === 0 ? (
          <div className="empty" style={{ padding: 40 }}>
            <div className="empty-i">📄</div>
            <h3>Draft is empty</h3>
            <p>Open Composer to start authoring.</p>
          </div>
        ) : (
          <div className="preview-doc">{items.map(renderItem)}</div>
        )}
      </div>
      </div>
    </RenderStyleContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// Per-item render
// ---------------------------------------------------------------------------

export function renderFinalDraftItem(item: FinalDraftItem) {
  return renderItem(item);
}

function renderItem(item: FinalDraftItem) {
  if (item.type === "section_heading") {
    const lvl = Math.max(0, Math.min(5, item.level));
    const sizes = [
      "1.5rem",
      "1.2rem",
      "1.05rem",
      "0.95rem",
      "0.88rem",
      "0.82rem",
    ];
    return (
      <h3
        key={item.id}
        style={{
          margin: lvl <= 1 ? "20px 0 8px 0" : "14px 0 6px 0",
          fontSize: sizes[lvl],
          fontWeight: 700,
          color: "var(--text1)",
          paddingBottom: lvl <= 1 ? 4 : 0,
          borderBottom: lvl <= 1 ? "1px solid var(--border)" : undefined,
        }}
      >
        {item.title}
      </h3>
    );
  }
  if (item.type === "block") {
    return (
      <div key={item.id} style={{ margin: "6px 0" }}>
        <BlockRenderer blocks={[item.block]} />
      </div>
    );
  }
  if (item.type === "figure") {
    const src = item.figure.image_url.startsWith("http")
      ? item.figure.image_url
      : `${API_BASE}${item.figure.image_url}`;
    return (
      <figure
        key={item.id}
        style={{
          margin: "12px 0",
          textAlign: "center",
        }}
      >
        <img
          src={src}
          alt={item.figure.label || "figure"}
          loading="lazy"
          style={{
            maxWidth: "100%",
            height: "auto",
            border: "1px solid var(--border)",
            borderRadius: 4,
          }}
        />
        {(item.figure.label || item.figure.caption) && (
          <figcaption
            style={{
              fontSize: "0.74rem",
              color: "var(--text3)",
              fontStyle: "italic",
              marginTop: 4,
            }}
          >
            {item.figure.label && <b>{item.figure.label}</b>}
            {item.figure.label && item.figure.caption && " — "}
            {item.figure.caption}
          </figcaption>
        )}
      </figure>
    );
  }
  if (item.type === "question") {
    return <QuestionCard key={item.id} q={item.question} />;
  }
  // custom_text
  return (
    <div
      key={item.id}
      style={{
        margin: "8px 0",
        padding: "8px 12px",
        background: "rgba(91,108,255,0.04)",
        border: "1px solid rgba(91,108,255,0.2)",
        borderRadius: 4,
      }}
    >
      <RichText text={item.content} />
    </div>
  );
}

// PreviewQuestion replaced by shared <QuestionCard> from components/.
