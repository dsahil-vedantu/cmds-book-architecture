import { useEffect, useMemo, useState } from "react";
import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";

import {
  useFinalDraft,
  usePatchFinalDraft,
  useReseedFinalDraft,
} from "../api/hooks";
import { useUI } from "../stores/ui";
import { api, API_BASE } from "../api/client";
import type {
  FinalDraftItem,
  FinalDraftOperation,
} from "../api/client";
import { BlockRenderer } from "../components/BlockRenderer";
import { QuestionCard } from "../components/QuestionCard";
import { RichText } from "../components/RichText";

/**
 * Phase 3.3 — Composer UI.
 *
 * Read+edit interface over the FinalDraft items list. Each item carries a
 * stable id; the composer maintains optimistic local state and PATCHes
 * the backend in the background. Drag-drop reorders, ✏ edits inline, 🗑
 * removes, "+ Add custom text" inserts between any two items.
 *
 * Auto-save: any operation immediately fires PATCH; while the request is
 * in flight the local state is treated as canonical so the UI stays
 * responsive.
 */
export function FinalComposerPage() {
  const { selectedBookId, setView } = useUI();
  const { data, isLoading, error } = useFinalDraft(selectedBookId, true);
  const patch = usePatchFinalDraft(selectedBookId);
  const reseed = useReseedFinalDraft(selectedBookId);

  // Local optimistic items — replaces server state for instant UX.
  const [localItems, setLocalItems] = useState<FinalDraftItem[] | null>(null);
  useEffect(() => {
    if (data?.items && localItems === null) {
      setLocalItems(data.items);
    }
  }, [data?.items, localItems]);
  // Sync server -> local when backend pushes new state (e.g. after reseed)
  useEffect(() => {
    if (data?.items) setLocalItems(data.items);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.updated_at]);

  const items = localItems ?? data?.items ?? [];

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
  );

  const applyOps = (ops: FinalDraftOperation[], next: FinalDraftItem[]) => {
    setLocalItems(next);
    patch.mutate(ops);
  };

  const onDragEnd = (e: DragEndEvent) => {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    const oldIdx = items.findIndex((it) => it.id === active.id);
    const newIdx = items.findIndex((it) => it.id === over.id);
    if (oldIdx < 0 || newIdx < 0) return;
    const next = arrayMove(items, oldIdx, newIdx);
    const afterId =
      newIdx === 0
        ? "start"
        : (next[newIdx - 1].id as string | "start");
    applyOps(
      [{ op: "reorder", id: String(active.id), after_id: afterId }],
      next,
    );
  };

  const removeItem = (id: string) => {
    if (!window.confirm("Remove this item from the draft?")) return;
    const next = items.filter((it) => it.id !== id);
    applyOps([{ op: "remove", id }], next);
  };

  const editItem = (id: string, patchPayload: Record<string, unknown>) => {
    const next = items.map((it) =>
      it.id === id ? ({ ...it, ...patchPayload } as FinalDraftItem) : it,
    );
    applyOps([{ op: "edit_item", id, patch: patchPayload }], next);
  };

  const insertCustomTextAfter = (afterId: string | "start", content = "") => {
    // Optimistic: insert a placeholder; backend will replace with real id
    // on PATCH. Simpler: just fire PATCH and let onSuccess overwrite local.
    patch.mutate([{ op: "insert_custom_text", after_id: afterId, content }], {
      onSuccess: (d) => setLocalItems(d.items),
    });
  };

  const onReseed = () => {
    if (
      !window.confirm(
        "Re-seed the draft from the current Final Merge state? Your edits will be discarded.",
      )
    )
      return;
    reseed.mutate(true, {
      onSuccess: (d) => setLocalItems(d.items),
    });
  };

  if (!selectedBookId) {
    return (
      <div className="empty" style={{ padding: 40 }}>
        <div className="empty-i">🛠</div>
        <h3>Pick a book from the sidebar</h3>
      </div>
    );
  }
  if (isLoading) {
    return (
      <div className="empty" style={{ padding: 40 }}>
        <div className="empty-i">⏳</div>
        <h3>Loading draft…</h3>
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

  return (
    <div className="cnt">
      <div className="ci" style={{ maxWidth: 980, padding: "16px 22px" }}>
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
            🛠 Compose · Draft
          </h2>
          <span style={{ fontSize: "0.72rem", color: "var(--text3)" }}>
            {items.length} items
            {data.last_seeded_at && (
              <> · seeded {new Date(data.last_seeded_at).toLocaleString()}</>
            )}
            {patch.isPending && (
              <span style={{ marginLeft: 8, color: "var(--accent)" }}>
                ✎ saving…
              </span>
            )}
          </span>
          <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <button
              className="btn"
              style={{ fontSize: "0.72rem", padding: "4px 12px" }}
              onClick={() => setView("preview")}
              title="Open draft-aware preview (what your export will look like)"
            >
              👁 Preview
            </button>
            <button
              className="btn primary"
              style={{
                fontSize: "0.72rem",
                padding: "4px 12px",
                background: "var(--accent, #5b6cff)",
                color: "#fff",
              }}
              onClick={() => {
                if (
                  !window.confirm(
                    "Merge regenerated content into the draft? Every section that has a saved theory/question regeneration will be pulled in at its exact schema position, replacing originals. Your manual edits to the draft will be lost.",
                  )
                )
                  return;
                reseed.mutate(true, {
                  onSuccess: (d) => setLocalItems(d.items),
                });
              }}
              disabled={reseed.isPending}
              title="Pull all saved regen content into the draft at each section's exact position (same place the original sits)."
            >
              {reseed.isPending ? "Merging…" : "🔀 Merge regen"}
            </button>
            <button
              className="btn"
              style={{ fontSize: "0.72rem", padding: "4px 12px" }}
              onClick={onReseed}
              disabled={reseed.isPending}
              title="Discard edits and rebuild from current Final Merge"
            >
              {reseed.isPending ? "Re-seeding…" : "🔄 Re-seed"}
            </button>
            <a
              className="btn"
              href={api.finalDraftExportUrl(selectedBookId, "json")}
              download
              style={{ fontSize: "0.72rem", padding: "4px 12px" }}
              title="Download draft as JSON (the items list verbatim)"
            >
              ⬇ .json
            </a>
            <a
              className="btn"
              href={api.finalDraftExportUrl(selectedBookId, "markdown")}
              download
              style={{ fontSize: "0.72rem", padding: "4px 12px" }}
              title="Download draft as Markdown"
            >
              ⬇ .md
            </a>
            <a
              className="btn primary"
              href={api.finalDraftExportUrl(selectedBookId, "docx")}
              download
              style={{ fontSize: "0.72rem", padding: "4px 12px" }}
              title="Download draft as Word DOCX — embedded figures, native equations & tables"
            >
              ⬇ .docx
            </a>
          </div>
        </div>

        {/* Add at start */}
        <AddSlot
          onAdd={(content) => insertCustomTextAfter("start", content)}
        />

        {/* Sortable list */}
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={onDragEnd}
        >
          <SortableContext
            items={items.map((it) => it.id)}
            strategy={verticalListSortingStrategy}
          >
            {items.map((item) => (
              <SortableRow
                key={item.id}
                item={item}
                onRemove={() => removeItem(item.id)}
                onEdit={(payload) => editItem(item.id, payload)}
                onAddAfter={(content) =>
                  insertCustomTextAfter(item.id, content)
                }
              />
            ))}
          </SortableContext>
        </DndContext>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sortable row
// ---------------------------------------------------------------------------

function SortableRow({
  item,
  onRemove,
  onEdit,
  onAddAfter,
}: {
  item: FinalDraftItem;
  onRemove: () => void;
  onEdit: (patch: Record<string, unknown>) => void;
  onAddAfter: (content: string) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: item.id });
  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.55 : 1,
  };
  const [editing, setEditing] = useState(false);

  return (
    <>
      <div
        ref={setNodeRef}
        style={{
          ...style,
          display: "flex",
          gap: 8,
          alignItems: "stretch",
          margin: "4px 0",
        }}
      >
        {/* Drag handle */}
        <div
          {...attributes}
          {...listeners}
          style={{
            width: 18,
            cursor: "grab",
            color: "var(--text3)",
            fontSize: "0.78rem",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            userSelect: "none",
          }}
          title="Drag to reorder"
        >
          ⋮⋮
        </div>
        {/* Content */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <ItemBadge item={item} />
          {editing ? (
            <ItemEditor
              item={item}
              onCancel={() => setEditing(false)}
              onSave={(patch) => {
                onEdit(patch);
                setEditing(false);
              }}
            />
          ) : (
            <ItemPreview item={item} />
          )}
        </div>
        {/* Actions */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 4,
            alignItems: "flex-end",
            paddingTop: 4,
          }}
        >
          {!editing && (
            <button
              type="button"
              className="btn bg"
              style={{ fontSize: "0.62rem", padding: "1px 6px" }}
              onClick={() => setEditing(true)}
              title="Edit this item"
            >
              ✏ Edit
            </button>
          )}
          <button
            type="button"
            className="btn bg"
            style={{
              fontSize: "0.62rem",
              padding: "1px 6px",
              color: "var(--red, #d33)",
            }}
            onClick={onRemove}
            title="Remove from draft"
          >
            🗑 Remove
          </button>
        </div>
      </div>
      <AddSlot onAdd={onAddAfter} compact />
    </>
  );
}

// ---------------------------------------------------------------------------
// Per-type preview (the rendered, non-editing view)
// ---------------------------------------------------------------------------

function ItemBadge({ item }: { item: FinalDraftItem }) {
  const kind =
    item.type === "section_heading"
      ? `§ Section · L${item.level}`
      : item.type === "block"
        ? `¶ ${item.block.t}`
        : item.type === "figure"
          ? `🖼 Figure`
          : item.type === "question"
            ? `❓ Question`
            : `✎ Custom text`;
  return (
    <div
      style={{
        fontSize: "0.6rem",
        color: "var(--text3)",
        fontFamily: "var(--mono)",
        marginBottom: 2,
      }}
    >
      {kind}
      {item.type === "section_heading" && item.regen && " · ✨ regen"}
    </div>
  );
}

function ItemPreview({ item }: { item: FinalDraftItem }) {
  if (item.type === "section_heading") {
    return (
      <div
        style={{
          fontSize: item.level <= 1 ? "1.1rem" : "0.95rem",
          fontWeight: 700,
          padding: "4px 8px",
          background: "var(--bg2, #f5f5fa)",
          borderRadius: 4,
        }}
      >
        {item.title}
      </div>
    );
  }
  if (item.type === "block") {
    return (
      <div
        style={{
          fontSize: "0.82rem",
          padding: "4px 8px",
          border: "1px solid var(--border)",
          borderRadius: 4,
        }}
      >
        <BlockRenderer blocks={[item.block]} />
      </div>
    );
  }
  if (item.type === "figure") {
    return <ComposerFigureCard item={item} />;
  }
  if (item.type === "question") {
    // Reuse shared QuestionCard so the formatting matches Final / Preview.
    return <QuestionCard q={item.question} defaultSolutionOpen={false} />;
  }
  // custom_text
  return (
    <div
      style={{
        padding: "6px 10px",
        border: "1px dashed var(--accent, #5b6cff)",
        borderRadius: 4,
        background: "rgba(91,108,255,0.04)",
      }}
    >
      <RichText text={item.content || "(empty)"} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-type editor
// ---------------------------------------------------------------------------

function ItemEditor({
  item,
  onSave,
  onCancel,
}: {
  item: FinalDraftItem;
  onSave: (patch: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(() => stateForItem(item));
  const save = () => onSave(patchForItem(item, draft));

  const buttons = (
    <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
      <button
        type="button"
        className="btn primary"
        style={{ fontSize: "0.66rem", padding: "2px 10px" }}
        onClick={save}
      >
        Save
      </button>
      <button
        type="button"
        className="btn bg"
        style={{ fontSize: "0.66rem", padding: "2px 10px" }}
        onClick={onCancel}
      >
        Cancel
      </button>
    </div>
  );

  if (item.type === "section_heading") {
    return (
      <div>
        <input
          value={draft.title as string}
          onChange={(e) => setDraft({ ...draft, title: e.target.value })}
          style={{ width: "100%", fontSize: "0.9rem", padding: 4 }}
        />
        {buttons}
      </div>
    );
  }
  if (item.type === "block") {
    return (
      <div>
        <textarea
          value={draft.body as string}
          onChange={(e) => setDraft({ ...draft, body: e.target.value })}
          rows={6}
          style={{
            width: "100%",
            fontFamily: "var(--mono)",
            fontSize: "0.78rem",
            padding: 6,
          }}
        />
        <div
          style={{
            marginTop: 4,
            padding: 6,
            border: "1px solid var(--border)",
            borderRadius: 4,
            fontSize: "0.72rem",
          }}
        >
          <div style={{ color: "var(--text3)", marginBottom: 2 }}>Preview:</div>
          <RichText text={draft.body as string} />
        </div>
        {buttons}
      </div>
    );
  }
  if (item.type === "figure") {
    return (
      <div>
        <label style={{ fontSize: "0.7rem", color: "var(--text3)" }}>
          Label
        </label>
        <input
          value={draft.label as string}
          onChange={(e) => setDraft({ ...draft, label: e.target.value })}
          style={{ width: "100%", marginBottom: 4 }}
        />
        <label style={{ fontSize: "0.7rem", color: "var(--text3)" }}>
          Caption
        </label>
        <textarea
          value={draft.caption as string}
          onChange={(e) => setDraft({ ...draft, caption: e.target.value })}
          rows={2}
          style={{ width: "100%" }}
        />
        {buttons}
      </div>
    );
  }
  if (item.type === "question") {
    return (
      <div>
        <label style={{ fontSize: "0.7rem", color: "var(--text3)" }}>
          Question text
        </label>
        <textarea
          value={draft.raw_text as string}
          onChange={(e) => setDraft({ ...draft, raw_text: e.target.value })}
          rows={4}
          style={{
            width: "100%",
            fontFamily: "var(--mono)",
            fontSize: "0.78rem",
            padding: 6,
          }}
        />
        <label style={{ fontSize: "0.7rem", color: "var(--text3)" }}>
          Solution
        </label>
        <textarea
          value={draft.solution_text as string}
          onChange={(e) =>
            setDraft({ ...draft, solution_text: e.target.value })
          }
          rows={6}
          style={{
            width: "100%",
            fontFamily: "var(--mono)",
            fontSize: "0.78rem",
            padding: 6,
          }}
        />
        {buttons}
      </div>
    );
  }
  // custom_text
  return (
    <div>
      <textarea
        value={draft.content as string}
        onChange={(e) => setDraft({ ...draft, content: e.target.value })}
        rows={4}
        placeholder="Markdown allowed — $math$, **bold**, lists, etc."
        style={{
          width: "100%",
          fontFamily: "var(--mono)",
          fontSize: "0.78rem",
          padding: 6,
        }}
      />
      <div
        style={{
          marginTop: 4,
          padding: 6,
          border: "1px solid var(--border)",
          borderRadius: 4,
          fontSize: "0.72rem",
        }}
      >
        <div style={{ color: "var(--text3)", marginBottom: 2 }}>Preview:</div>
        <RichText text={draft.content as string} />
      </div>
      {buttons}
    </div>
  );
}

function stateForItem(item: FinalDraftItem): Record<string, unknown> {
  if (item.type === "section_heading") return { title: item.title };
  if (item.type === "block") return { body: blockBody(item.block as unknown) };
  if (item.type === "figure")
    return {
      label: item.figure.label || "",
      caption: item.figure.caption || "",
    };
  if (item.type === "question")
    return {
      raw_text: item.question.raw_text || "",
      solution_text: item.question.solution_text || "",
    };
  return { content: item.content || "" };
}

function patchForItem(
  item: FinalDraftItem,
  draft: Record<string, unknown>,
): Record<string, unknown> {
  if (item.type === "section_heading") return { title: draft.title };
  if (item.type === "block") {
    const block = setBlockBody(item.block, String(draft.body ?? ""));
    return { block };
  }
  if (item.type === "figure") {
    return {
      figure: {
        ...item.figure,
        label: String(draft.label ?? ""),
        caption: String(draft.caption ?? ""),
      },
    };
  }
  if (item.type === "question") {
    return {
      question: {
        ...item.question,
        raw_text: String(draft.raw_text ?? ""),
        solution_text: String(draft.solution_text ?? ""),
      },
    };
  }
  return { content: draft.content };
}

function blockBody(block: unknown): string {
  const b = block as { t: string; c?: string; term?: string; items?: string[] };
  if (b.t === "p" || b.t === "h3" || b.t === "eq" || b.t === "kp")
    return String(b.c ?? "");
  if (b.t === "def") return String(b.c ?? "");
  if (b.t === "list") return (b.items ?? []).join("\n");
  return JSON.stringify(b, null, 2);
}

function setBlockBody(block: unknown, body: string): unknown {
  const b = block as { t: string; c?: string; items?: string[] };
  if (b.t === "list") {
    return { ...b, items: body.split(/\n+/).filter(Boolean) };
  }
  return { ...b, c: body };
}

// ---------------------------------------------------------------------------
// Figure card — preview + "🔁 Regenerate" touchpoint
// ---------------------------------------------------------------------------
//
// Per user request: in the Composer, each figure item gets a quick link to
// the regen flow (which still lives on the Images page — section-level
// regen). Clicking the button:
//   1. Selects the figure's parent section_id on the Figures sidebar
//   2. Navigates to view="images"
// User then clicks "🔁 Regenerate figures" on the Figures page to actually
// kick off the regen job. We don't trigger regen from here directly
// because the existing UI on the Figures page has params (style guidance,
// custom instructions) the user may want to set per-run.

function ComposerFigureCard({
  item,
}: {
  item: Extract<FinalDraftItem, { type: "figure" }>;
}) {
  const { setView, selectFigureSection } = useUI();
  const src = item.figure.image_url.startsWith("http")
    ? item.figure.image_url
    : `${API_BASE}${item.figure.image_url}`;

  const openInImagesForRegen = () => {
    if (item.parent_section_id) {
      selectFigureSection(item.parent_section_id);
    }
    setView("images");
  };

  return (
    <div
      style={{
        padding: 6,
        border: "1px solid var(--border)",
        borderRadius: 4,
        maxWidth: 320,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          marginBottom: 4,
        }}
      >
        {item.figure.label && (
          <span style={{ fontSize: "0.68rem", fontWeight: 600 }}>
            {item.figure.label}
          </span>
        )}
        <button
          type="button"
          onClick={openInImagesForRegen}
          title="Open this figure on the Images page to regenerate it (regen params + custom instructions are set there)"
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: "1px solid var(--accent, #5b6cff)",
            borderRadius: 4,
            color: "var(--accent, #5b6cff)",
            fontSize: "0.6rem",
            fontWeight: 600,
            padding: "1px 6px",
            cursor: "pointer",
          }}
        >
          🔁 Regenerate
        </button>
      </div>
      <img
        src={src}
        alt={item.figure.label || "figure"}
        loading="lazy"
        style={{ maxWidth: "100%", display: "block", borderRadius: 4 }}
      />
      {item.figure.caption && (
        <div
          style={{
            fontSize: "0.66rem",
            fontStyle: "italic",
            color: "var(--text3)",
            marginTop: 2,
          }}
        >
          {item.figure.caption}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Insertion slot
// ---------------------------------------------------------------------------

function AddSlot({
  onAdd,
  compact,
}: {
  onAdd: (content: string) => void;
  compact?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState("");
  if (!open) {
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          padding: compact ? "1px 0" : "4px 0",
        }}
      >
        <button
          type="button"
          onClick={() => setOpen(true)}
          style={{
            background: "transparent",
            border: "1px dashed var(--border)",
            borderRadius: 4,
            color: "var(--text3)",
            fontSize: "0.62rem",
            padding: "1px 8px",
            cursor: "pointer",
          }}
          title="Insert a custom text block here"
        >
          + Add
        </button>
      </div>
    );
  }
  return (
    <div
      style={{
        margin: "6px 0",
        padding: 8,
        border: "1px dashed var(--accent, #5b6cff)",
        borderRadius: 4,
      }}
    >
      <textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        autoFocus
        placeholder="Markdown allowed — $math$, **bold**, lists, etc."
        rows={3}
        style={{
          width: "100%",
          fontFamily: "var(--mono)",
          fontSize: "0.78rem",
          padding: 6,
        }}
      />
      <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
        <button
          type="button"
          className="btn primary"
          style={{ fontSize: "0.66rem", padding: "2px 10px" }}
          onClick={() => {
            if (content.trim()) onAdd(content);
            setContent("");
            setOpen(false);
          }}
        >
          Insert
        </button>
        <button
          type="button"
          className="btn bg"
          style={{ fontSize: "0.66rem", padding: "2px 10px" }}
          onClick={() => {
            setContent("");
            setOpen(false);
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
