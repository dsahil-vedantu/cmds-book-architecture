import { useState, useMemo, createContext, useContext } from "react";
import {
  useBooks,
  useBook,
  useSections,
  useBookRegenerations,
  useQuestionRegenerations,
  useRegenQuestions,
  useQuestionBanks,
  useQuestions,
  useBookFigures,
  useBookUnattachedFigures,
} from "../api/hooks";
import type {
  Section,
  QuestionKind,
  QuestionBankSectionGroup,
  FigureSectionGroup,
} from "../api/client";
import { useUI } from "../stores/ui";

export function Sidebar() {
  const { data: books, isLoading } = useBooks();
  const { view, setView, selectedBookId, selectBook } = useUI();
  const [query, setQuery] = useState("");

  const SIDEBAR_STATUSES = new Set([
    "analysing", "schema_ready", "extracting", "ready", "failed",
  ]);
  const filtered = books
    ?.filter((b) => SIDEBAR_STATUSES.has(b.status))
    .filter((b) => b.title.toLowerCase().includes(query.toLowerCase()));

  return (
    <aside className="sb">
      <div className="sb-head">
        <div className="sb-top">
          <div className="sb-logo">📚</div>
          <div>
            <div className="sb-t1">Book Folder</div>
            <div className="sb-t2">Academic Content Library</div>
          </div>
        </div>
        <div className="sb-srch">
          <span style={{ fontSize: "0.7rem", color: "var(--text3)" }}>🔍</span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search books…"
          />
        </div>
      </div>

      <div className="sb-scroll">
        <button className="sb-new" onClick={() => setView("upload")}>
          <span>+</span>
          <span>Upload a new book</span>
        </button>

        <div className="sb-lbl">Navigate</div>
        <button
          className={`sb-nav-btn ${view === "library" ? "active" : ""}`}
          onClick={() => setView("library")}
        >
          <span>🏠</span>
          <span>Library</span>
        </button>
        <button
          className={`sb-nav-btn ${view === "settings" ? "active" : ""}`}
          onClick={() => setView("settings")}
        >
          <span>⚙️</span>
          <span>OCR Providers</span>
        </button>

        <div className="sb-lbl">Books</div>
        {isLoading && (
          <div style={{ padding: "4px 14px", fontSize: "0.72rem", color: "var(--text3)" }}>
            Loading…
          </div>
        )}
        {filtered?.length === 0 && (
          <div style={{ padding: "4px 14px", fontSize: "0.72rem", color: "var(--text3)" }}>
            {query ? "No matches" : "No books yet"}
          </div>
        )}
        {filtered?.map((b) => (
          <div key={b.id}>
            <button
              className={`tn ${selectedBookId === b.id ? "active" : ""}`}
              onClick={() => {
                selectBook(b.id);
                setView(b.status === "schema_ready" ? "schema" : "reader");
              }}
            >
              <span className="tarr">▸</span>
              <span className="tico">📘</span>
              <span className="tlbl">{b.title}</span>
              <span className="tcnt">{shortStatus(b.status)}</span>
            </button>
            {selectedBookId === b.id && <BookFolders bookId={b.id} />}
          </div>
        ))}
      </div>
    </aside>
  );
}

function shortStatus(s: string): string {
  if (s === "schema_ready") return "sch";
  if (s === "extracting") return "ext";
  if (s === "ready") return "ok";
  if (s === "failed") return "err";
  return s.slice(0, 3);
}

function BookFolders({ bookId }: { bookId: string }) {
  const { data: regens } = useBookRegenerations(bookId);
  const { data: banks } = useQuestionBanks(bookId);
  const { data: unattached } = useBookUnattachedFigures(bookId);
  const { bookLens, setBookLens, view, setView } = useUI();
  const unattachedCount = unattached?.figures?.length ?? 0;

  const latestRegen = regens?.[0] ?? null;
  // Prefer the latest READY bank so the user sees results even if a retry
  // is currently in-flight. Fall back to whatever most-recent bank exists.
  const latestReady = banks?.find((b) => b.status === "ready") ?? null;
  const latestBank = latestReady ?? banks?.[0] ?? null;
  const bankReady = latestBank?.status === "ready";
  // A NEWER (retry) bank is extracting on top of the ready one
  const inFlightRetry = !!(
    latestReady &&
    banks &&
    banks.some(
      (b) =>
        (b.status === "extracting" || b.status === "pending") &&
        new Date(b.created_at).getTime() > new Date(latestReady.created_at).getTime(),
    )
  );

  return (
    <div style={{ marginLeft: 14 }}>
      {/* Schema / Progress — always reachable from any book view */}
      <button
        className={`sb-nav-btn ${view === "schema" ? "active" : ""}`}
        onClick={() => setView("schema")}
        style={{
          width: "100%",
          padding: "4px 8px",
          marginBottom: 4,
          fontSize: "0.7rem",
          fontWeight: 600,
          textAlign: "left",
          border: "1px solid var(--b1)",
          borderRadius: 5,
          background: view === "schema" ? "var(--accent)" : "var(--bg2)",
          color: view === "schema" ? "#fff" : "var(--text2)",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
        title="Schema & extraction progress for this book"
      >
        <span>🗂</span>
        <span>Schema / Progress</span>
      </button>
      {/* Phase 3 — Compose Final Draft (top-level book action, separate from
          the read-only lenses below). Drag-drop reorder, edit, remove,
          add custom text. Auto-seeds from Final on first open. */}
      <button
        className={`sb-nav-btn ${view === "compose" ? "active" : ""}`}
        onClick={() => setView("compose")}
        style={{
          width: "100%",
          padding: "4px 8px",
          marginBottom: 4,
          fontSize: "0.7rem",
          fontWeight: 600,
          textAlign: "left",
          border: "1px solid var(--b1)",
          borderRadius: 5,
          background:
            view === "compose"
              ? "var(--accent, #5b6cff)"
              : "rgba(91,108,255,0.08)",
          color: view === "compose" ? "#fff" : "var(--text1)",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: 6,
        }}
        title="Compose & export the final chapter — drag-drop reorder, edit, remove, add"
      >
        <span>🛠</span>
        <span>Compose Final Draft</span>
      </button>
      {/* Lens toggle — Theory vs Questions */}
      <div
        style={{
          display: "flex",
          gap: 4,
          padding: "4px 6px",
          marginBottom: 4,
        }}
      >
        <button
          className={`sb-lens ${bookLens === "theory" ? "active" : ""}`}
          onClick={() => {
            // Sync the page view with the sidebar lens — otherwise the
            // currently-mounted page (e.g. QuestionsPage) stays rendered
            // while the sidebar shows theory sections, causing a blank
            // state until the user refreshes. Mirrors Images button.
            setBookLens("theory");
            setView("reader");
          }}
          style={lensBtnStyle(bookLens === "theory")}
          title="Theory — extracted section content"
        >
          📄 Theory
        </button>
        <button
          className={`sb-lens ${bookLens === "questions" ? "active" : ""}`}
          onClick={() => {
            setBookLens("questions");
            setView("questions");
          }}
          disabled={!latestBank}
          style={lensBtnStyle(bookLens === "questions", !latestBank)}
          title={
            latestBank
              ? inFlightRetry
                ? "Questions — showing last ready bank (a re-extraction is running)"
                : "Questions — per-section folders by kind"
              : "Extract questions first from the Schema page"
          }
        >
          ❓ Questions
          {inFlightRetry && (
            <span style={{ marginLeft: 4, fontSize: "0.6rem", opacity: 0.8 }}>
              ⟳
            </span>
          )}
        </button>
        {/* Figures pipeline v2 lens — additive, no behavior change to
            theory/questions tabs. Clicking jumps straight to the figures
            page (no inline tree — figures get their own full-width view). */}
        <button
          className={`sb-lens ${bookLens === "images" ? "active" : ""}`}
          onClick={() => {
            setBookLens("images");
            setView("images");
          }}
          style={lensBtnStyle(bookLens === "images")}
          title={
            unattachedCount > 0
              ? `Images — ${unattachedCount} unattached figure${unattachedCount === 1 ? "" : "s"} need review`
              : "Images — extracted figures + regenerated variants"
          }
        >
          🖼 Images
          {unattachedCount > 0 && (
            <span
              style={{
                marginLeft: 6,
                display: "inline-block",
                padding: "0 5px",
                fontSize: "0.62rem",
                fontWeight: 700,
                borderRadius: 8,
                background: "var(--red, #d33)",
                color: "white",
                lineHeight: "14px",
                verticalAlign: "middle",
              }}
            >
              ⚠ {unattachedCount}
            </span>
          )}
        </button>
        {/* Phase 2 — Final merged view (read-only). Clicking jumps to the
            FinalMergePage which renders theory + figures + questions in
            schema order with export buttons. */}
        <button
          className={`sb-lens ${view === "final" ? "active" : ""}`}
          onClick={() => setView("final")}
          style={lensBtnStyle(view === "final")}
          title="Final merged view — theory + questions + figures, export-ready"
        >
          📄 Final
        </button>
      </div>
      {inFlightRetry && bookLens === "questions" && (
        <div
          style={{
            padding: "4px 8px",
            marginBottom: 4,
            fontSize: "0.66rem",
            color: "var(--text3)",
            background: "var(--bg2)",
            borderRadius: 4,
            fontStyle: "italic",
          }}
        >
          ⟳ Re-extraction running — showing last ready bank
        </div>
      )}

      {bookLens === "theory" ? (
        <>
          <SectionList bookId={bookId} regenId={null} />
          {latestRegen && (
            <>
              <div className="sb-lbl" style={{ marginTop: 6 }}>✨ Regenerated</div>
              <SectionList bookId={bookId} regenId={latestRegen.id} />
            </>
          )}
        </>
      ) : bookLens === "images" ? (
        // 🖼 Images mode — same shape as Theory/Questions tab:
        // schema-mirrored tree (filtered to figure-containing nodes)
        // followed by a ✨ Regenerated subtree (filtered to nodes that
        // have at least one approved variant).
        <FiguresLens bookId={bookId} />
      ) : (
        <>
          {!latestBank && (
            <div style={{ padding: "4px 14px", fontSize: "0.7rem", color: "var(--text3)" }}>
              No question bank yet — trigger extraction from the Schema page.
            </div>
          )}
          {latestBank && !bankReady && (
            <div style={{ padding: "4px 14px", fontSize: "0.7rem", color: "var(--text3)" }}>
              {latestBank.status === "failed"
                ? `Extraction failed${latestBank.last_error ? `: ${latestBank.last_error.slice(0, 60)}…` : ""}`
                : "Extracting…"}
            </div>
          )}
          {bankReady && (
            <>
              <QuestionsLens
                bankId={latestBank.id}
                bookId={bookId}
                regenId={null}
              />
              {/* ✨ Regenerated — exact tree replica of the Questions lens,
                   sourced from the latest regen run's questions. Same shape
                   as theory's flat-list mirror but tree-shaped here because
                   the question schema is hierarchical. */}
              <RegenLabelAndLens bookId={bookId} bankId={latestBank.id} />
            </>
          )}
        </>
      )}
    </div>
  );
}

function lensBtnStyle(active: boolean, disabled = false): React.CSSProperties {
  return {
    flex: 1,
    padding: "4px 8px",
    fontSize: "0.7rem",
    fontWeight: 600,
    border: "1px solid var(--b1)",
    borderRadius: 5,
    background: active ? "var(--accent)" : "var(--bg2)",
    color: active ? "#fff" : disabled ? "var(--text3)" : "var(--text2)",
    cursor: disabled ? "not-allowed" : "pointer",
    opacity: disabled ? 0.5 : 1,
    transition: "background 0.15s",
  };
}

// -------------------------------------------------------------------
// Theory lens — the original flat section list (unchanged behaviour)
// -------------------------------------------------------------------
function SectionList({ bookId, regenId }: { bookId: string; regenId: string | null }) {
  const { data: sections } = useSections(bookId);
  const { data: book } = useBook(bookId);
  const { data: regen } = useBookRegenerations(bookId);
  const { selectedSectionId, selectedRegenId, selectSection, setView, setRegenId } = useUI();

  const latestRegen = regen?.[0] ?? null;
  const blocksBySection = regenId && latestRegen
    ? (latestRegen.blocks_by_section as Record<string, unknown[]>) ?? {}
    : null;

  const ordered = useMemo(() => {
    if (!sections) return [];
    const schemaIds: string[] = [];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    function walk(arr: any[]) {
      for (const s of arr ?? []) {
        if (s.type !== "excluded") schemaIds.push(s.id as string);
        walk(s.subsections ?? []);
      }
    }
    walk(book?.schema_?.sections ?? []);
    // Hide question-kind child sections from the Theory tab — they're
    // rendered as inline touchpoint chips inside their parent section's
    // theory blocks. Theory-aid kinds (illustration, progress-check,
    // activity, try-it, quick-check, thinking-corner, note) are KEPT
    // because they're part of theory and have content_types=["theory"].
    // Pattern matches the suffix "<kind>-<num>" or just "<kind>" at end of id.
    // Question kinds (must match Step 4 linker `_QUESTION_KINDS`):
    //   example, worked-example, solved-example, exercise, problem,
    //   practice-problem, in-text-question, intext-question
    const QUESTION_KIND_RE =
      /[\.\-](?:worked-example|solved-example|practice-problem|in-text-question|intext-question|example|exercise|problem)(?:[\.\-]\d[\w.-]*)?$/;
    const isQuestionChild = (id: string) => QUESTION_KIND_RE.test(id);
    const filteredSections = sections.filter((s) => !isQuestionChild(s.section_id));
    if (schemaIds.length === 0) return filteredSections;
    const map = new Map(filteredSections.map((s) => [s.section_id, s]));
    const result = schemaIds
      .filter((id) => !isQuestionChild(id))
      .map((id) => map.get(id))
      .filter(Boolean) as Section[];
    const inOrder = new Set(schemaIds);
    filteredSections.forEach((s) => { if (!inOrder.has(s.section_id)) result.push(s); });
    return result;
  }, [sections, book]);

  const visible = regenId && blocksBySection
    ? ordered.filter((s) => blocksBySection[s.section_id] !== undefined)
    : ordered;

  if (!visible || visible.length === 0) return null;

  const isRegenFolder = !!regenId;

  return (
    <div>
      {visible.map((s: Section) => {
        const isActive = selectedSectionId === s.id &&
          (isRegenFolder ? selectedRegenId === regenId : selectedRegenId === null);

        return (
          <button
            key={`${regenId ?? "orig"}-${s.id}`}
            className={`tn ${isActive ? "active" : ""}`}
            onClick={() => {
              selectSection(s.id);
              setRegenId(isRegenFolder ? regenId : null);
              setView("reader");
            }}
          >
            <span className="tarr"> </span>
            <span className="tico">
              {isRegenFolder
                ? "✨"
                : s.status === "failed" ? "⚠️" : s.status === "passed" ? "✅" : "📄"}
            </span>
            <span className="tlbl">{s.title}</span>
          </button>
        );
      })}
    </div>
  );
}

// -------------------------------------------------------------------
// Questions lens — schema sections with per-kind subfolders
// -------------------------------------------------------------------
const KIND_META: Record<QuestionKind, { icon: string; label: string }> = {
  example: { icon: "📘", label: "Examples" },
  problem: { icon: "🧪", label: "Problems" },
  try_it: { icon: "💡", label: "Try It" },
  exercise: { icon: "📝", label: "Exercises" },
  review: { icon: "🔁", label: "Review" },
  mcq: { icon: "🔘", label: "MCQs" },
  other: { icon: "❓", label: "Other" },
};

// Display order inside a section (matches how textbooks usually present them).
const KIND_ORDER: QuestionKind[] = [
  "example", "try_it", "problem", "mcq", "exercise", "review", "other",
];

// Tree node we build by walking the schema. Each schema node (section +
// subsection) becomes a tree node, with its extraction group attached if any
// Context flag: when set, this Questions tree is the regen mirror, not the
// original. Tree node click handlers consult it to switch to regen view.
const QuestionsRegenContext = createContext<{
  regenId: string | null;
  regenBankId: string | null;
  activeBankId: string;
} | null>(null);

// questions were extracted for that section_ref.
type SchemaTreeNode = {
  id: string;
  title: string;
  type: string;
  depth: number;
  children: SchemaTreeNode[];
  group: QuestionBankSectionGroup | null;
};

function QuestionsLens({
  bankId,
  bookId,
  regenId = null,
}: {
  bankId: string;
  bookId: string;
  regenId?: string | null;
}) {
  // R1 — when regenId is set, the tree is rendered in "regen mode": same
  // schema hierarchy, but the per-section group is sourced from regen
  // variants instead of original extraction. Clicks navigate to the
  // regen review pane for that section.
  const { data: bankDetail } = useQuestions(bankId);
  const { data: regenData } = useRegenQuestions(regenId);
  const { data: book } = useBook(bookId);
  // Normalise: bankDetail and regenData both expose `sections[]` with
  // {section_ref, section_title, questions}. Pick whichever matches mode.
  const detail = regenId
    ? regenData
      ? ({ sections: regenData.sections } as { sections: QuestionBankSectionGroup[] })
      : undefined
    : bankDetail;

  // Build a hierarchical tree mirroring the schema 1:1, with extraction
  // groups attached. Every schema node is rendered — even ones with zero
  // questions — so missed examples are visible, not hidden.
  const tree: SchemaTreeNode[] = useMemo(() => {
    if (!book?.schema_?.sections) return [];
    const groupByRef = new Map<string, QuestionBankSectionGroup>();
    for (const s of detail?.sections ?? []) {
      groupByRef.set(s.section_ref, s);
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    function build(node: any, depth: number): SchemaTreeNode | null {
      if ((node.type ?? "section") === "excluded") return null;
      // Chapter wrapper → flatten its children up one level (matches worker)
      if ((node.type ?? "").toLowerCase() === "chapter") {
        // We synthesise a chapter node so it still appears in the tree at
        // the top level, but with depth=0 and its children at depth=1.
        const kids: SchemaTreeNode[] = [];
        for (const c of node.subsections ?? []) {
          const built = build(c, depth + 1);
          if (built) kids.push(built);
        }
        return {
          id: node.id,
          title: node.title,
          type: node.type ?? "chapter",
          depth,
          children: kids,
          group: groupByRef.get(node.id) ?? null,
        };
      }
      const kids: SchemaTreeNode[] = [];
      for (const c of node.subsections ?? []) {
        const built = build(c, depth + 1);
        if (built) kids.push(built);
      }
      return {
        id: node.id,
        title: node.title,
        type: node.type ?? "section",
        depth,
        children: kids,
        group: groupByRef.get(node.id) ?? null,
      };
    }
    const out: SchemaTreeNode[] = [];
    for (const s of book.schema_.sections) {
      const built = build(s, 0);
      if (built) out.push(built);
    }
    return out;
  }, [book, detail]);

  // Append any extracted groups whose section_ref ISN'T in the schema —
  // typically excluded blocks ("PRACTICE QUESTIONS: …") that are linked to
  // sections by a separate process. Render them as a flat list at the bottom.
  const orphans = useMemo(() => {
    if (!detail) return [];
    const inSchema = new Set<string>();
    function collect(n: SchemaTreeNode) {
      inSchema.add(n.id);
      n.children.forEach(collect);
    }
    tree.forEach(collect);
    return detail.sections.filter((s) => !inSchema.has(s.section_ref));
  }, [detail, tree]);

  if (!detail) {
    return (
      <div style={{ padding: "4px 14px", fontSize: "0.7rem", color: "var(--text3)" }}>
        {regenId ? "Loading regenerated questions…" : "Loading questions…"}
      </div>
    );
  }
  if (tree.length === 0 && orphans.length === 0) {
    return (
      <div style={{ padding: "4px 14px", fontSize: "0.7rem", color: "var(--text3)" }}>
        {regenId
          ? "No regenerated questions yet."
          : "No questions extracted yet."}
      </div>
    );
  }

  // Resolve the bank that owns this regen so click navigation can hop
  // banks if the regen is from an older one (cross-bank case).
  const regenBankId = regenId
    ? (regenData?.regen?.bank_id ?? bankId)
    : null;

  return (
    <QuestionsRegenContext.Provider
      value={{ regenId, regenBankId, activeBankId: bankId }}
    >
    <div>
      {tree.map((n) => (
        <QuestionTreeNode key={n.id} node={n} />
      ))}
      {orphans.length > 0 && (
        <>
          <div className="sb-lbl" style={{ marginTop: 6 }}>
            End-of-chapter blocks
          </div>
          {(() => {
            // Group "PARENT::CHILD" orphans under a synthetic parent node so
            // practice sub-headings (Very Short / Short / Essay …) render as
            // nested folders matching the original PDF layout.
            type Group = { parent: string; standalone: QuestionBankSectionGroup | null; children: QuestionBankSectionGroup[] };
            const byParent = new Map<string, Group>();
            const ordered: string[] = [];
            for (const s of orphans) {
              const [parent, ...rest] = s.section_ref.split("::");
              if (!byParent.has(parent)) {
                byParent.set(parent, { parent, standalone: null, children: [] });
                ordered.push(parent);
              }
              const g = byParent.get(parent)!;
              if (rest.length === 0) g.standalone = s;
              else g.children.push(s);
            }
            return ordered.map((p) => {
              const g = byParent.get(p)!;
              if (g.children.length === 0 && g.standalone) {
                return <QuestionFlatNode key={p} section={g.standalone} />;
              }
              return <QuestionParentNode key={p} parent={p} children={g.children} />;
            });
          })()}
        </>
      )}
    </div>
    </QuestionsRegenContext.Provider>
  );
}

/** Recursive tree node — mirrors schema hierarchy with depth-indent.
 *  Renders even when the section has 0 extracted questions, so missed
 *  examples are visible (shown as "0" with muted styling). */
function QuestionTreeNode({ node }: { node: SchemaTreeNode }) {
  const hasChildren = node.children.length > 0;
  // Auto-open the top two levels so the user sees the structure without
  // clicking. Deeper levels start closed.
  const [open, setOpen] = useState(node.depth < 2);
  const {
    selectedQuestionSectionRef,
    selectedKind,
    selectedQuestionRegenId,
    selectKind,
    selectQuestionRegen,
    selectBank,
    setView,
  } = useUI();
  // Regen context — when set, this tree is the ✨ mirror; clicks navigate
  // to the regen review pane.
  const regenCtx = useContext(QuestionsRegenContext);
  const inRegenMode = !!regenCtx?.regenId;

  const group = node.group;
  const ownCount = group?.questions.length ?? 0;
  // Sum extracted count of ALL descendants — gives a "this whole subtree
  // produced N questions" badge on parent rows.
  const totalCount = useMemo(() => {
    let n = ownCount;
    function add(c: SchemaTreeNode) {
      n += c.group?.questions.length ?? 0;
      c.children.forEach(add);
    }
    node.children.forEach(add);
    return n;
  }, [node, ownCount]);

  // Visual cue when the schema expected questions but extraction got zero.
  // In regen mode, "no variants" is dimmed; in original mode it means
  // "missed" (extraction found nothing for a schema-listed section).
  const isMissed = ownCount === 0 && !hasChildren;
  const indentPx = Math.min(node.depth, 4) * 10;
  const isExample = /^\s*EXAMPLE\s+\d/i.test(node.title);
  const isActive =
    selectedQuestionSectionRef === node.id &&
    !selectedKind &&
    (inRegenMode
      ? selectedQuestionRegenId === regenCtx?.regenId
      : !selectedQuestionRegenId);

  // Click target: in original mode, clear any regen + show extraction.
  // In regen mode, hop bank if needed, set the regen and section so
  // RegenView opens that section's diff pane.
  const navigate = () => {
    if (inRegenMode && regenCtx) {
      if (regenCtx.regenBankId && regenCtx.regenBankId !== regenCtx.activeBankId) {
        selectBank(regenCtx.regenBankId);
      }
      selectQuestionRegen(regenCtx.regenId);
    } else {
      selectQuestionRegen(null);
    }
    selectKind(node.id, null);
    setView("questions");
  };

  return (
    <div>
      <div
        className={`tn ${isActive ? "active" : ""}`}
        style={{
          paddingLeft: 10 + indentPx,
          opacity: isMissed ? 0.55 : 1,
          display: "flex",
          alignItems: "center",
          cursor: "pointer",
        }}
        onClick={navigate}
        title={
          isMissed
            ? `${node.title} — no questions extracted (schema-listed but Gemini found none)`
            : node.title
        }
      >
        <span
          className={`tarr ${open ? "o" : ""}`}
          onClick={(e) => {
            e.stopPropagation();
            if (hasChildren) setOpen((o) => !o);
          }}
          style={{ cursor: hasChildren ? "pointer" : "default" }}
        >
          {hasChildren ? "▸" : " "}
        </span>
        <span className="tico">
          {inRegenMode
            ? hasChildren ? "📂" : "✨"
            : isExample ? "📘" : hasChildren ? "📂" : "📖"}
        </span>
        <span className="tlbl" style={{ fontSize: node.depth === 0 ? "0.78rem" : "0.72rem" }}>
          {node.title}
        </span>
        <span
          className="tcnt"
          style={{ color: isMissed ? "var(--warn, #c80)" : undefined }}
        >
          {hasChildren ? totalCount : ownCount}
        </span>
      </div>
      {open && hasChildren && (
        <>
          {node.children.map((c) => (
            <QuestionTreeNode key={c.id} node={c} />
          ))}
        </>
      )}
    </div>
  );
}

/** Synthetic parent node for excluded sections that have nested
 *  sub-headings (e.g. "PRACTICE QUESTIONS: CLASSROOM WING" with
 *  "Very Short / Short / Essay" children). Each child is rendered as a
 *  QuestionFlatNode beneath the parent, mirroring the PDF structure. */
function QuestionParentNode({
  parent,
  children,
}: {
  parent: string;
  children: QuestionBankSectionGroup[];
}) {
  const [open, setOpen] = useState(true);
  const {
    selectedQuestionSectionRef,
    selectedKind,
    selectedQuestionRegenId,
    selectKind,
    selectQuestionRegen,
    selectBank,
    setView,
  } = useUI();
  const regenCtx = useContext(QuestionsRegenContext);
  const inRegenMode = !!regenCtx?.regenId;
  const total = children.reduce((n, c) => n + c.questions.length, 0);
  const isActive =
    selectedQuestionSectionRef === parent &&
    !selectedKind &&
    (inRegenMode
      ? selectedQuestionRegenId === regenCtx?.regenId
      : !selectedQuestionRegenId);
  const onClick = () => {
    if (inRegenMode && regenCtx) {
      if (regenCtx.regenBankId && regenCtx.regenBankId !== regenCtx.activeBankId) {
        selectBank(regenCtx.regenBankId);
      }
      selectQuestionRegen(regenCtx.regenId);
    } else {
      selectQuestionRegen(null);
    }
    selectKind(parent, null);
    setView("questions");
  };
  return (
    <div>
      <div
        className={`tn ${isActive ? "active" : ""}`}
        style={{ display: "flex", alignItems: "center", cursor: "pointer" }}
        onClick={onClick}
        title={`View all ${total} ${inRegenMode ? "regen variants" : "questions"} in ${parent}`}
      >
        <span
          className={`tarr ${open ? "o" : ""}`}
          onClick={(e) => {
            e.stopPropagation();
            setOpen((o) => !o);
          }}
          style={{ cursor: "pointer" }}
        >
          ▸
        </span>
        <span className="tico">📂</span>
        <span className="tlbl" style={{ fontWeight: 600 }}>{parent}</span>
        <span className="tcnt">{total}</span>
      </div>
      {open && (
        <div style={{ marginLeft: 12 }}>
          {children.map((c) => {
            const childTitle =
              c.section_title ||
              c.section_ref.split("::").slice(1).join("::") ||
              c.section_ref;
            return (
              <QuestionFlatNode
                key={c.section_ref}
                section={{ ...c, section_title: childTitle }}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------
// "✨ Regenerated" label + a second QuestionsLens render in regen mode.
// The same tree component is re-used so the regen subtree is a 1:1
// structural replica of the original Questions tree — only the per-row
// content (counts, click target) differs. Same UX pattern as theory's
// SectionList(regenId) double-render.
// ---------------------------------------------------------------------
function RegenLabelAndLens({ bookId, bankId }: { bookId: string; bankId: string }) {
  const { data: regens } = useQuestionRegenerations(bookId);
  if (!regens || regens.length === 0) return null;
  const latestRegen = regens[0];
  if (!latestRegen) return null;
  return (
    <>
      <div className="sb-lbl" style={{ marginTop: 6 }}>✨ Regenerated</div>
      <QuestionsLens
        bankId={bankId}
        bookId={bookId}
        regenId={latestRegen.id}
      />
    </>
  );
}


/** Flat node for end-of-chapter excluded blocks that aren't part of the
 *  schema tree — kept simple, no nesting. */
function QuestionFlatNode({ section }: { section: QuestionBankSectionGroup }) {
  const {
    selectedQuestionSectionRef,
    selectedKind,
    selectedQuestionRegenId,
    selectKind,
    selectQuestionRegen,
    selectBank,
    setView,
  } = useUI();
  const regenCtx = useContext(QuestionsRegenContext);
  const inRegenMode = !!regenCtx?.regenId;

  const totalCount = section.questions.length;
  const isActive =
    selectedQuestionSectionRef === section.section_ref &&
    !selectedKind &&
    (inRegenMode
      ? selectedQuestionRegenId === regenCtx?.regenId
      : !selectedQuestionRegenId);

  const onClick = () => {
    if (inRegenMode && regenCtx) {
      if (regenCtx.regenBankId && regenCtx.regenBankId !== regenCtx.activeBankId) {
        selectBank(regenCtx.regenBankId);
      }
      selectQuestionRegen(regenCtx.regenId);
    } else {
      selectQuestionRegen(null);
    }
    selectKind(section.section_ref, null);
    setView("questions");
  };

  return (
    <div>
      <button
        className={`tn ${isActive ? "active" : ""}`}
        onClick={onClick}
        title={`${totalCount} ${inRegenMode ? "regen variants" : "questions"} in ${section.section_title || section.section_ref}`}
      >
        <span className="tarr"> </span>
        <span className="tico">{inRegenMode ? "✨" : "📑"}</span>
        <span className="tlbl">{section.section_title || section.section_ref}</span>
        <span className="tcnt">{totalCount}</span>
      </button>
    </div>
  );
}


// ============================================================
// 🖼 FiguresLens — schema-mirrored tree for the Images tab sidebar.
//
// Mirrors the Theory + Questions sidebar shape exactly:
//   - Top tree:  schema, filtered to nodes containing ≥1 extracted figure
//   - ✨ Regenerated subtree (only shown when ≥1 approved variant exists):
//     same shape, filtered to nodes containing ≥1 approved regen
//
// Click a leaf node → sets selectedFigureSectionRef + ensures view="images"
// so the FiguresPage main panel renders that section's figure cards.
// ============================================================

function FiguresLens({ bookId }: { bookId: string }) {
  const { data: book } = useBook(bookId);
  const { data: figData } = useBookFigures(bookId);

  const sections = figData?.sections ?? [];
  const totalApproved = sections.reduce((n, s) => n + s.n_approved, 0);
  const totalExtracted = figData?.total_figures ?? 0;

  if (totalExtracted === 0) {
    return (
      <div style={{ padding: "8px 14px", fontSize: "0.7rem", color: "var(--text3)" }}>
        <div style={{ fontWeight: 600, marginBottom: 4, color: "var(--text2)" }}>
          🖼 No figures yet
        </div>
        <div style={{ lineHeight: 1.5 }}>
          Click <b>🖼 Extract figures</b> in the main panel to run the pipeline.
        </div>
      </div>
    );
  }

  return (
    <div>
      <FiguresTreeSidebar
        bookId={bookId}
        schema={book?.schema_ as FiguresSchemaLike | undefined}
        sections={sections}
        mode="original"
      />
      {totalApproved > 0 && (
        <>
          <div className="sb-lbl" style={{ marginTop: 6 }}>✨ Regenerated</div>
          <FiguresTreeSidebar
            bookId={bookId}
            schema={book?.schema_ as FiguresSchemaLike | undefined}
            sections={sections.filter((s) => s.n_approved > 0)}
            mode="regenerated"
          />
        </>
      )}
    </div>
  );
}

// Schema-mirrored tree for figures (separate from QuestionsLens to avoid
// coupling). Filters nodes to those whose section_ref appears in
// `byRef` (either original mode → all sections-with-figures, or
// regenerated mode → only sections with ≥1 approved variant).

type FiguresSchemaNodeLike = {
  id: string;
  title: string;
  type?: string;
  subsections?: FiguresSchemaNodeLike[];
};
type FiguresSchemaLike = { sections?: FiguresSchemaNodeLike[] };

type FiguresTreeNodeShape = {
  id: string;
  title: string;
  depth: number;
  children: FiguresTreeNodeShape[];
  group: FigureSectionGroup | null;
};

function buildFiguresSidebarTree(
  schema: FiguresSchemaLike | undefined,
  byRef: Map<string, FigureSectionGroup>,
): FiguresTreeNodeShape[] {
  if (!schema?.sections) {
    // No schema available — just emit a flat list of the sections we have.
    return [...byRef.entries()].map(([ref, g]) => ({
      id: ref,
      title: ref,
      depth: 0,
      children: [],
      group: g,
    }));
  }
  function walk(node: FiguresSchemaNodeLike, depth: number): FiguresTreeNodeShape | null {
    const kids = (node.subsections ?? [])
      .map((c) => walk(c, depth + 1))
      .filter((c): c is FiguresTreeNodeShape => c !== null);
    const group = byRef.get(node.id) ?? null;
    if (!group && kids.length === 0) return null;
    return {
      id: node.id,
      title: node.title ?? node.id,
      depth,
      children: kids,
      group,
    };
  }
  const out: FiguresTreeNodeShape[] = [];
  for (const s of schema.sections ?? []) {
    const built = walk(s, 0);
    if (built) out.push(built);
  }
  // Append orphans not in schema
  const inTree = new Set<string>();
  function collect(n: FiguresTreeNodeShape) {
    inTree.add(n.id);
    n.children.forEach(collect);
  }
  out.forEach(collect);
  for (const [ref, g] of byRef) {
    if (!inTree.has(ref)) {
      out.push({ id: ref, title: ref, depth: 0, children: [], group: g });
    }
  }
  return out;
}

function FiguresTreeSidebar({
  bookId,
  schema,
  sections,
  mode,
}: {
  bookId: string;
  schema: FiguresSchemaLike | undefined;
  sections: FigureSectionGroup[];
  mode: "original" | "regenerated";
}) {
  const byRef = useMemo(() => {
    const m = new Map<string, FigureSectionGroup>();
    for (const s of sections) m.set(s.section_ref, s);
    return m;
  }, [sections]);
  const tree = useMemo(
    () => buildFiguresSidebarTree(schema, byRef),
    [schema, byRef],
  );
  return (
    <>
      {tree.map((n) => (
        <FiguresTreeRow
          key={`${mode}-${n.id}`}
          node={n}
          mode={mode}
          bookId={bookId}
        />
      ))}
    </>
  );
}

function FiguresTreeRow({
  node,
  mode,
  bookId,
}: {
  node: FiguresTreeNodeShape;
  mode: "original" | "regenerated";
  bookId: string;
}) {
  const hasChildren = node.children.length > 0;
  const [open, setOpen] = useState(node.depth < 2);
  const {
    view,
    selectedFigureSectionRef,
    selectFigureSection,
    setView,
    setBookLens,
  } = useUI();
  const isLeafWithGroup = !!node.group;
  const isActive =
    view === "images"
    && selectedFigureSectionRef === node.id
    && isLeafWithGroup;

  // Subtree figure count for badge
  const subtreeCount = useMemo(() => {
    let n = node.group?.figures.length ?? 0;
    function add(c: FiguresTreeNodeShape) {
      n += c.group?.figures.length ?? 0;
      c.children.forEach(add);
    }
    node.children.forEach(add);
    return n;
  }, [node]);

  const navigate = () => {
    if (!isLeafWithGroup) return;
    selectFigureSection(node.id);
    setBookLens("images");
    setView("images");
  };

  // Suppress unused-var warning for bookId (kept in signature for parity
  // with other lens components + future per-book filtering hooks)
  void bookId;

  return (
    <div>
      <div
        className={`tn ${isActive ? "active" : ""}`}
        style={{
          paddingLeft: 10 + Math.min(node.depth, 4) * 10,
          display: "flex",
          alignItems: "center",
          cursor: "pointer",
        }}
        onClick={isLeafWithGroup ? navigate : undefined}
        title={node.title}
      >
        <span
          className={`tarr ${open ? "o" : ""}`}
          onClick={(e) => {
            e.stopPropagation();
            if (hasChildren) setOpen((o) => !o);
          }}
          style={{ cursor: hasChildren ? "pointer" : "default" }}
        >
          {hasChildren ? "▸" : " "}
        </span>
        <span className="tico">
          {isLeafWithGroup ? "🖼" : hasChildren ? "📂" : "📄"}
        </span>
        <span
          className="tlbl"
          style={{ fontSize: node.depth === 0 ? "0.78rem" : "0.72rem" }}
        >
          {node.title}
        </span>
        {node.group && (
          <span style={{ display: "flex", gap: 2 }}>
            {mode === "regenerated" ? (
              <FigChip kind="approved" count={node.group.n_approved} />
            ) : (
              <>
                {node.group.n_theory > 0 && (
                  <FigChip kind="theory" count={node.group.n_theory} />
                )}
                {node.group.n_question > 0 && (
                  <FigChip kind="question" count={node.group.n_question} />
                )}
              </>
            )}
          </span>
        )}
        <span className="tcnt">{subtreeCount}</span>
      </div>
      {open && hasChildren && (
        <>
          {node.children.map((c) => (
            <FiguresTreeRow
              key={`${mode}-${c.id}`}
              node={c}
              mode={mode}
              bookId={bookId}
            />
          ))}
        </>
      )}
    </div>
  );
}

function FigChip({
  kind,
  count,
}: {
  kind: "theory" | "question" | "approved";
  count: number;
}) {
  const palette = {
    theory: { bg: "rgba(26,54,110,0.12)", color: "var(--accent)", label: "T" },
    question: { bg: "rgba(155,89,182,0.12)", color: "#9b59b6", label: "Q" },
    approved: { bg: "rgba(46,204,113,0.14)", color: "#16a085", label: "✓" },
  } as const;
  const s = palette[kind];
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        padding: "0 4px",
        borderRadius: 3,
        fontSize: "0.55rem",
        fontWeight: 600,
        background: s.bg,
        color: s.color,
        textTransform: "uppercase",
        letterSpacing: 0.3,
      }}
    >
      {s.label}
      {count > 1 && <span style={{ opacity: 0.7, marginLeft: 2 }}>×{count}</span>}
    </span>
  );
}
