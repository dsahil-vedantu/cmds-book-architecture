import { useEffect, useMemo, useState } from "react";
import {
  useBook,
  useBulkDeleteRegenQuestions,
  useCreateQuestionBank,
  useDeleteQuestionBank,
  useDeleteQuestionRegeneration,
  useJob,
  useQuestionBank,
  useQuestionBanks,
  useQuestionRegen,
  useQuestionRegenerations,
  useQuestions,
  useQuestionStructure,
  useReExtractBlock,
  useRegenQuestions,
  useRestoreRejected,
  useRestoreAllRejected,
  useDiscardRejected,
  useHideFigureReference,
  useHideQuestion,
  useUnhideQuestion,
  useRetrySection,
  useRetryRegenSection,
  useSaveQuestionRegeneration,
  useStartQuestionRegeneration,
} from "../api/hooks";
import {
  api,
  API_BASE,
  type BookSchema,
  type EmbeddedFigure,
  type ExtractionSectionStats,
  type Question,
  type QuestionBank,
  type QuestionRegeneration,
  type SchemaSection,
  type UUID,
} from "../api/client";
import { useUI } from "../stores/ui";

// ----------------------------------------------------------------------
// Sub-part false-positive detector
//
// When a section has e.g. expected=3 / extracted=1, the literal math says
// `missed = 2`. But if the single extracted question carries sub-part
// markers like "(i) … (ii) … (iii) …" or "(a) … (b) … (c) …", the
// extractor was CORRECT to keep them as one row (per the prompt's
// sub-parts vs separate-questions distinction). The "missed" is a
// schema-analyse over-count, not a real miss.
//
// Used by the per-section badge AND the V3SummaryTable so the displayed
// "missed" matches what's really missing.
// ----------------------------------------------------------------------

const SUB_PART_RE = /\(\s*(?:i{1,3}|iv|v|vi{0,3}|ix|x|[a-d])\s*\)/gi;

function countSubParts(text: string | null | undefined): number {
  if (!text) return 0;
  const m = text.match(SUB_PART_RE);
  return m ? m.length : 0;
}

/** Returns the number of "missed" slots that are actually sub-parts of an
 *  existing extracted question (i.e. false positives). Caller subtracts
 *  this from the raw `missed` number to get the honest count.
 */
function subPartFalsePositives(
  questions: Array<{ raw_text?: string | null }>,
  rawMissed: number,
): number {
  if (rawMissed <= 0 || questions.length === 0) return 0;
  // Sum sub-parts found across kept questions, but only the surplus
  // beyond the question itself (i.e. (i)(ii)(iii) → 3 sub-parts → 2
  // "extra" markers that analyse over-counted).
  let total = 0;
  for (const q of questions) {
    const n = countSubParts(q.raw_text);
    if (n >= 2) total += n - 1;  // n sub-parts = 1 real Q + (n-1) "missed"
  }
  return Math.min(rawMissed, total);
}

/** Sum of false-positive "missed" across all sections — used for bank-level totals. */
function aggregateSubPartFalsePositives(
  sections: ReadonlyArray<{
    questions: ReadonlyArray<{ raw_text?: string | null }>;
    missed?: number;
  }> | null | undefined,
): number {
  if (!sections) return 0;
  let total = 0;
  for (const s of sections) {
    total += subPartFalsePositives(
      s.questions as Array<{ raw_text?: string | null }>,
      s.missed ?? 0,
    );
  }
  return total;
}

export function QuestionsPage() {
  const {
    selectedBookId,
    selectedBankId,
    selectBank,
    selectedQuestionRegenId,
    selectQuestionRegen,
    selectedQuestionSectionRef,
    selectedExcludedBlockRef,
    selectedKind,
  } = useUI();
  const { data: book } = useBook(selectedBookId);
  const { data: banks } = useQuestionBanks(selectedBookId);
  const anyExtracting = !!banks?.some(
    (b) => b.status === "pending" || b.status === "extracting",
  );
  const { data: structure } = useQuestionStructure(selectedBookId, {
    pollWhileExtracting: anyExtracting,
  });

  // Auto-select latest bank if none picked
  useEffect(() => {
    if (!selectedBankId && banks && banks.length > 0) {
      selectBank(banks[0].id);
    }
  }, [banks, selectedBankId, selectBank]);

  const createBank = useCreateQuestionBank();
  const deleteBank = useDeleteQuestionBank();

  const { data: bank } = useQuestionBank(selectedBankId);
  const { data: detail, refetch: refetchDetail } = useQuestions(selectedBankId, {
    bankStatus: bank?.status,
  });

  // Refetch grouped questions on every bank change while extracting, and once
  // more when it flips to ready, so folders appear block-by-block.
  useEffect(() => {
    if (bank?.status === "extracting" || bank?.status === "ready") {
      void refetchDetail();
    }
  }, [bank?.status, bank?.stats?.total_extracted, refetchDetail]);

  if (!book) {
    return (
      <>
        <div className="topbar">
          <div className="bc">
            <span className="bci a">Questions</span>
          </div>
        </div>
        <div className="cnt">
          <div className="ci">
            <div className="empty">
              <div className="empty-i">❓</div>
              <h3>Pick a book from the sidebar</h3>
            </div>
          </div>
        </div>
      </>
    );
  }

  const isExtracting = bank?.status === "pending" || bank?.status === "extracting";
  const isReady = bank?.status === "ready";
  const isFailed = bank?.status === "failed";
  const hasBank = !!bank;
  const canCreate = !!book.schema_;

  return (
    <>
      <div className="topbar">
        <div className="bc">
          <span className="bci">{book.title}</span>
          <span className="bcs">›</span>
          <span className="bci a">Questions</span>
        </div>
        {bank?.stats && (() => {
          const rawMissed = bank.stats.missed ?? 0;
          const subParts = aggregateSubPartFalsePositives(detail?.sections);
          const adjMissed = Math.max(0, rawMissed - subParts);
          return (
            <span
              className="btn bg"
              title={`${bank.stats.total_extracted} extracted of ${bank.stats.total_identified} identified${
                adjMissed ? ` · ${adjMissed} missed` : ""
              }${subParts ? ` · ${subParts} sub-part${subParts === 1 ? "" : "s"} of grouped questions` : ""}`}
              style={{
                cursor: "default",
                color: adjMissed ? "var(--warn, #c80)" : "var(--text1)",
              }}
            >
              {bank.stats.total_extracted}/{bank.stats.total_identified}
              {adjMissed ? ` · ${adjMissed} missed` : ""}
            </span>
          );
        })()}
        {isReady && selectedBankId && (
          <>
            <button className="btn bg" onClick={() => api.exportQuestionsJson(selectedBankId)}>
              ⬇ .json
            </button>
            <button className="btn bg" onClick={() => api.exportQuestionsMarkdown(selectedBankId)}>
              ⬇ .md
            </button>
            <button className="btn bg" onClick={() => api.exportQuestionsDocx(selectedBankId)}>
              ⬇ .docx
            </button>
            <button
              className="btn bg"
              onClick={() => {
                if (!selectedBookId || !selectedBankId) return;
                if (!confirm("Delete this question bank and re-extract?")) return;
                deleteBank.mutate(
                  { bankId: selectedBankId, bookId: selectedBookId },
                  {
                    onSuccess: () => {
                      selectBank(null);
                      createBank.mutate(selectedBookId, {
                        onSuccess: (res) => selectBank(res.bank_id),
                      });
                    },
                  },
                );
              }}
            >
              ↺ Re-extract
            </button>
          </>
        )}
      </div>

      <div className="cnt">
        {/* When viewing a regen run, break out of the .ci max-width=750
            constraint so the side-by-side review uses the full content width
            (matches theory regen layout). */}
        <div
          className="ci"
          style={
            selectedQuestionRegenId
              ? { maxWidth: "none", padding: "16px 22px" }
              : undefined
          }
        >
          {/* Removed the big SECTION breadcrumb card — the sidebar tree
               and page header already show which section is active, and
               the right-pane title above each question list repeats the
               human-readable name. The slug-style ref banner was pure
               noise. */}
          {!hasBank && (
            <div className="empty">
              <div className="empty-i">❓</div>
              <h3>Extract questions from this book</h3>
              <p>
                Scans every section of the approved schema and OCRs every exercise,
                problem, or Q&amp;A item verbatim via Gemini.
              </p>
              {!canCreate && (
                <p style={{ color: "var(--red)", marginTop: 12 }}>
                  Run Analyse &amp; approve the schema first.
                </p>
              )}
              <button
                className="btn primary"
                style={{ marginTop: 20 }}
                disabled={!canCreate || createBank.isPending || !selectedBookId}
                onClick={() => {
                  if (!selectedBookId) return;
                  createBank.mutate(selectedBookId, {
                    onSuccess: (res) => selectBank(res.bank_id),
                  });
                }}
              >
                {createBank.isPending ? "Starting..." : "Extract Questions"}
              </button>
            </div>
          )}

          {isExtracting && bank && (
            <>
              <BankExtractionProgress
                status={bank.status}
                activeJobId={bank.active_job_id ?? null}
                fallback={bank.active_job ?? null}
                stats={bank.stats ?? null}
              />
              {detail && detail.total_questions > 0 && (
                <div style={{ marginTop: 12 }}>
                  <div
                    style={{
                      fontSize: "0.7rem",
                      color: "var(--text3)",
                      marginBottom: 10,
                      fontStyle: "italic",
                    }}
                  >
                    Showing {detail.total_questions} question
                    {detail.total_questions === 1 ? "" : "s"} extracted so far —
                    more will appear as each block completes.
                  </div>
                  <QuestionList
                    detail={detail}
                    bookSchema={book?.schema_ ?? null}
                    bankId={selectedBankId}
                    excludedBlockRef={selectedExcludedBlockRef}
                    scopedSectionRef={selectedQuestionSectionRef}
                    scopedKind={selectedKind}
                  />
                </div>
              )}
            </>
          )}

          {isFailed && bank && (
            <div className="empty">
              <div className="empty-i">⚠️</div>
              <h3>Extraction failed</h3>
              <p>Try again — the previous bank will be replaced.</p>
              <button
                className="btn primary"
                style={{ marginTop: 20 }}
                onClick={() => {
                  if (!selectedBookId) return;
                  deleteBank.mutate(
                    { bankId: bank.id, bookId: selectedBookId },
                    {
                      onSuccess: () => {
                        selectBank(null);
                        createBank.mutate(selectedBookId, {
                          onSuccess: (res) => selectBank(res.bank_id),
                        });
                      },
                    },
                  );
                }}
              >
                Retry
              </button>
            </div>
          )}

          {/* Extraction summary moved to the Schema page (post-extraction).
              Removed from the Questions page so the workspace stays
              focused on the questions themselves. */}

          {isReady && detail && selectedBankId && selectedBookId && (
            <RegenRunBar
              bankId={selectedBankId}
              bookId={selectedBookId}
              detail={detail}
              activeRegenId={selectedQuestionRegenId}
              onSelectRegen={(id) => selectQuestionRegen(id)}
            />
          )}

          {isReady && selectedQuestionRegenId && selectedBookId && (
            <RegenView
              regenId={selectedQuestionRegenId}
              bookId={selectedBookId}
              originalDetail={detail ?? null}
            />
          )}

          {isReady && detail && !selectedQuestionRegenId && (
            <QuestionList
              detail={detail}
              bookSchema={book?.schema_ ?? null}
              bankId={selectedBankId}
              excludedBlockRef={selectedExcludedBlockRef}
              scopedSectionRef={selectedQuestionSectionRef}
              scopedKind={selectedKind}
            />
          )}
        </div>
      </div>
    </>
  );
}


export function V3SummaryTable({
  stats,
}: {
  stats: NonNullable<QuestionBank["stats"]>;
  sections?: ReadonlyArray<{
    questions: ReadonlyArray<{ raw_text?: string | null }>;
    missed?: number;
  }> | null;
}) {
  if (!stats.totals) return null;
  const { expected_total, extracted_total } = stats.totals;

  const cell: React.CSSProperties = {
    padding: "6px 12px",
    fontSize: "0.78rem",
    borderBottom: "1px solid var(--b1, #eee)",
  };
  const labelCell: React.CSSProperties = { ...cell, color: "var(--text3, #888)" };
  const valueCell: React.CSSProperties = { ...cell, fontWeight: 600, textAlign: "right", fontVariantNumeric: "tabular-nums" };

  return (
    <div
      style={{
        display: "inline-block",
        marginBottom: 12,
        background: "var(--bg, #fff)",
        border: "1px solid var(--b1, #eee)",
        borderRadius: 6,
        overflow: "hidden",
        minWidth: 240,
      }}
    >
      <table style={{ borderCollapse: "collapse", width: "100%" }}>
        <tbody>
          <tr>
            <td style={labelCell}>Total Questions</td>
            <td style={valueCell}>{expected_total ?? "—"}</td>
          </tr>
          <tr>
            <td style={{ ...labelCell, borderBottom: "none" }}>Extracted</td>
            <td style={{ ...valueCell, borderBottom: "none", color: "var(--green, #2a9d5e)" }}>
              {extracted_total ?? 0}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}



function BankExtractionProgress({
  status,
  activeJobId,
  fallback,
  stats,
}: {
  status: string;
  activeJobId: UUID | null;
  fallback:
    | { id: UUID; status: string; progress: number | null; message: string | null }
    | null;
  stats:
    | {
        total_identified: number;
        total_extracted: number;
        missed: number;
        blocks: {
          excluded_block_index: number;
          title: string;
          page_start: number | null;
          page_end: number | null;
          identified: number;
          extracted: number;
          status: string;
        }[];
      }
    | null;
}) {
  // Poll the live extraction job every second so the heartbeat message
  // ("Extracting Exercise 1.1 — 45s elapsed") shows up in real time.
  const { data: job } = useJob(activeJobId, { pollMs: 1000 });
  const live = job ?? fallback;

  const progress = Math.max(0, Math.min(100, live?.progress ?? 0));
  const message =
    live?.message ||
    (status === "pending"
      ? "Queued…"
      : "Scanning sections — this can take a few minutes for a full book");

  const blocks = stats?.blocks ?? [];
  const done = blocks.filter((b) => b.status !== "failed").length;
  const totalBlocks = blocks.length;
  const lastFew = blocks.slice(-5).reverse();

  return (
    <div className="card">
      <div className="clbl">
        Extracting questions{" "}
        <span style={{ color: "var(--text3)", fontWeight: 500 }}>— {status}</span>
        {stats && (
          <span style={{ color: "var(--text3)", fontWeight: 500, marginLeft: 8 }}>
            · {stats.total_extracted}/{stats.total_identified} questions
            {totalBlocks > 0 ? ` · ${done}/${totalBlocks} blocks` : ""}
          </span>
        )}
      </div>
      <div className="prog">
        <div
          className="progb"
          style={{
            width: `${progress || 2}%`,
            minWidth: progress > 0 ? undefined : 40,
            background: progress > 0
              ? "var(--accent)"
              : "linear-gradient(90deg, var(--accent) 0%, var(--accent) 50%, transparent 100%)",
            animation: progress > 0 ? undefined : "progressShimmer 1.4s linear infinite",
            transition: "width 0.4s ease",
          }}
        />
      </div>
      <div className="progr" style={{ display: "flex", justifyContent: "space-between" }}>
        <span>{message}</span>
        <span style={{ color: "var(--text3)", fontFamily: "var(--mono)", fontSize: "0.7rem" }}>
          {progress}%
        </span>
      </div>
      {lastFew.length > 0 && (
        <div style={{ marginTop: 12, fontSize: "0.7rem", color: "var(--text3)" }}>
          <div style={{ marginBottom: 4, fontWeight: 600 }}>Recent blocks</div>
          {lastFew.map((b) => {
            const ok = b.status === "ok";
            const empty = b.status === "empty";
            const color = ok
              ? "var(--green, #2a9d5e)"
              : empty
              ? "var(--text3)"
              : "var(--warn, #c80)";
            const icon = ok ? "✓" : empty ? "–" : "!";
            return (
              <div
                key={b.excluded_block_index}
                style={{
                  display: "flex",
                  gap: 8,
                  lineHeight: 1.5,
                  fontFamily: "var(--mono)",
                  fontSize: "0.68rem",
                }}
              >
                <span style={{ color, width: 12 }}>{icon}</span>
                <span style={{ flex: 1 }}>{b.title}</span>
                <span style={{ color: "var(--text3)" }}>
                  {b.page_start ? `p.${b.page_start}` : ""}
                  {b.page_end && b.page_end !== b.page_start ? `–${b.page_end}` : ""}
                </span>
                <span style={{ color }}>
                  {b.extracted}/{b.identified}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const KIND_LABEL: Record<string, string> = {
  example: "Examples",
  problem: "Problems",
  try_it: "Try It",
  exercise: "Exercises",
  review: "Review",
  mcq: "MCQs",
  other: "Other",
};

// Matches prompt-emitted figure placeholders: {{fig: Fig 4.5 — caption}}.
// Case-insensitive on the prefix, tolerant of whitespace around the colon.
const FIG_RE = /\{\{\s*fig\s*:\s*([^}]+?)\s*\}\}/gi;

/** Split a raw_text on {{fig: ...}} tokens so we can render each figure as a
 *  visible chip inside the question body. */
// Render text with embedded-figure images spliced in at the touchpoint
// (the position where "Figure X.Y" appears in the text). Figures whose
// labels don't appear in this text are returned unconsumed so the caller
// can render them at the end of the question card.
function renderWithEmbeddedFigures(
  text: string,
  figures: EmbeddedFigure[],
): { nodes: (string | JSX.Element)[]; consumedIds: Set<string> } {
  const consumedIds = new Set<string>();
  if (!text || figures.length === 0) {
    return { nodes: renderWithFigures(text), consumedIds };
  }
  type Hit = { end: number; fig: EmbeddedFigure };
  const hits: Hit[] = [];
  for (const fig of figures) {
    if (!fig.label) continue;
    const num = fig.label.match(/(\d+(?:\.\d+)*)/)?.[1];
    if (!num) continue;
    const escaped = num.replace(/\./g, "\\.");
    // Accept: "Figure 4.2", "Fig 4.2", "Fig. 4.2", "Fig_4.2", "Fig:4.2",
    // "Figure4.2" (no separator), all case-insensitive. Anything between
    // the keyword and number is whitespace / dot / underscore / colon /
    // dash (zero or more) so we tolerate OCR variants.
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
    nodes.push(...renderWithFigures(text.slice(cursor, hit.end)));
    nodes.push(
      <QuestionFigure key={`emb-${hit.fig.figure_id}`} figure={hit.fig} />,
    );
    consumedIds.add(hit.fig.figure_id);
    cursor = hit.end;
  }
  nodes.push(...renderWithFigures(text.slice(cursor)));
  return { nodes, consumedIds };
}

function renderWithFigures(text: string): (string | JSX.Element)[] {
  if (!text) return [text];
  const parts: (string | JSX.Element)[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  FIG_RE.lastIndex = 0;
  let key = 0;
  while ((match = FIG_RE.exec(text)) !== null) {
    const [full, inner] = match;
    const start = match.index;
    if (start > lastIndex) parts.push(text.slice(lastIndex, start));
    parts.push(
      <span
        key={`fig-${key++}`}
        style={{
          display: "inline-block",
          padding: "1px 6px",
          margin: "0 2px",
          borderRadius: 4,
          background: "var(--bg2, #f2eaff)",
          color: "var(--purple, #6b3fd4)",
          fontSize: "0.7rem",
          fontFamily: "var(--mono)",
          border: "1px solid var(--purple, #b9a1eb)",
        }}
        title="Figure placeholder — exact position in the book"
      >
        🖼 {inner.trim()}
      </span>,
    );
    lastIndex = start + full.length;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts;
}

function QuestionList({
  detail,
  bookSchema,
  bankId,
  excludedBlockRef,
  scopedSectionRef,
  scopedKind,
}: {
  detail: NonNullable<ReturnType<typeof useQuestions>["data"]>;
  bookSchema: BookSchema | null;
  bankId: UUID | null;
  excludedBlockRef: string | null;
  scopedSectionRef: string | null;
  scopedKind: string | null;
}) {
  const { selectExcludedBlock, selectKind } = useUI();

  // -------- Kind-scoped view (from Questions lens sidebar) --------
  const kindScoped = useMemo(() => {
    if (!scopedKind || !scopedSectionRef) return null;
    const sec = detail.sections.find((s) => s.section_ref === scopedSectionRef);
    if (!sec) return null;
    const items = sec.by_kind?.[scopedKind as keyof typeof sec.by_kind] ?? [];
    return { sec, items };
  }, [detail, scopedKind, scopedSectionRef]);

  // -------- Section-scoped view (sidebar click on a header) --------
  // Hoisted above the early returns so every render calls the same hooks
  // in the same order (React's Rules of Hooks). Previously these useMemo
  // calls lived AFTER `if (kindScoped) return ...` and the next early
  // return, which caused hook-count mismatch → blank screen in prod build.
  const sectionScoped = useMemo(() => {
    if (!scopedSectionRef || scopedKind) return null;
    const prefix = scopedSectionRef + "::";
    const matches = detail.sections.filter(
      (s) => s.section_ref === scopedSectionRef || s.section_ref.startsWith(prefix),
    );
    if (matches.length === 0) return null;
    return matches;
  }, [detail, scopedSectionRef, scopedKind]);

  type BlockStat = NonNullable<typeof detail.stats>["blocks"][number];
  const blocksBySection = useMemo(() => {
    const blocks: BlockStat[] = detail.stats?.blocks ?? [];
    const out: Record<string, BlockStat[]> = {};
    for (const b of blocks) {
      const key = b.section_ref || "__unlinked__";
      (out[key] ||= []).push(b);
    }
    return out;
  }, [detail.stats]);

  const v3StatsBySection = useMemo(() => {
    const out: Record<string, ExtractionSectionStats> = {};
    for (const s of detail.stats?.sections ?? []) {
      out[s.section_ref] = s;
    }
    return out;
  }, [detail.stats]);

  const schemaOrder = useMemo(() => {
    const out: { sectionRef: string; depth: number; title: string }[] = [];
    const walk = (secs: SchemaSection[], depth: number) => {
      for (const s of secs) {
        if ((s.type ?? "section") !== "excluded") {
          out.push({ sectionRef: s.id, depth, title: s.title });
        }
        if (s.subsections && s.subsections.length > 0) {
          walk(s.subsections, depth + 1);
        }
      }
    };
    walk(bookSchema?.sections ?? [], 0);
    return out;
  }, [bookSchema]);

  const scoped = useMemo(() => {
    if (!excludedBlockRef) return null;
    const filteredSections = detail.sections
      .map((sec) => ({
        ...sec,
        questions: sec.questions.filter(
          (q) => q.excluded_block_ref === excludedBlockRef,
        ),
      }))
      .filter((sec) => sec.questions.length > 0);
    const blockStat = (detail.stats?.blocks ?? []).find(
      (b) => `ex-${b.excluded_block_index}` === excludedBlockRef,
    );
    return { sections: filteredSections, blockStat };
  }, [detail, excludedBlockRef]);

  if (kindScoped) {
    const label = KIND_LABEL[scopedKind!] ?? scopedKind!;
    return (
      <>
        <div className="cvh">
          <div className="cvt">
            Scoped to{" "}
            <b>
              {label} · §{kindScoped.sec.section_ref} {kindScoped.sec.section_title}
            </b>
            <button
              className="btn bg"
              style={{
                marginLeft: 10,
                fontSize: "0.66rem",
                padding: "2px 8px",
              }}
              onClick={() => selectKind(null, null)}
            >
              Clear filter
            </button>
          </div>
        </div>
        {kindScoped.items.length === 0 ? (
          <div className="empty" style={{ padding: 30 }}>
            <div className="empty-i">📝</div>
            <h3>No {label.toLowerCase()} in this section</h3>
          </div>
        ) : (
          <SectionBlock
            sec={{ ...kindScoped.sec, questions: kindScoped.items }}
            blocks={[]}
            v3Stat={null}
            bankId={bankId}
          />
        )}
      </>
    );
  }

  if (sectionScoped) {
    const totalQ = sectionScoped.reduce((n, s) => n + s.questions.length, 0);
    const headerTitle =
      sectionScoped.find((s) => s.section_ref === scopedSectionRef)?.section_title ||
      scopedSectionRef!;
    return (
      <>
        <div className="cvh">
          <div className="cvt">
            Scoped to <b>{headerTitle}</b> · {totalQ} question{totalQ === 1 ? "" : "s"}
            <button
              className="btn bg"
              style={{ marginLeft: 10, fontSize: "0.66rem", padding: "2px 8px" }}
              onClick={() => selectKind(null, null)}
            >
              Clear filter
            </button>
          </div>
        </div>
        {totalQ === 0 ? (
          <div className="empty" style={{ padding: 30 }}>
            <div className="empty-i">📝</div>
            <h3>No questions extracted in this section yet</h3>
            {bankId && scopedSectionRef && (
              <EmptySectionRetryButton bankId={bankId} sectionRef={scopedSectionRef} />
            )}
          </div>
        ) : (
          sectionScoped.map((sec) => (
            <SectionBlock
              key={sec.section_ref}
              sec={sec}
              blocks={[]}
              v3Stat={null}
              bankId={bankId}
              displayTitle={
                sec.section_ref === scopedSectionRef
                  ? sec.section_title
                  : sec.section_title ||
                    sec.section_ref.split("::").slice(1).join("::") ||
                    sec.section_ref
              }
            />
          ))
        )}
      </>
    );
  }

  if (scoped) {
    return (
      <>
        <div className="cvh">
          <div className="cvt">
            Scoped to{" "}
            <b>
              {scoped.blockStat?.title ?? excludedBlockRef}
            </b>
            <button
              className="btn bg"
              style={{
                marginLeft: 10,
                fontSize: "0.66rem",
                padding: "2px 8px",
              }}
              onClick={() => selectExcludedBlock(null, null)}
            >
              Clear filter
            </button>
          </div>
        </div>
        {scoped.sections.length === 0 ? (
          <div className="empty" style={{ padding: 30 }}>
            <div className="empty-i">📝</div>
            <h3>No questions extracted from this block</h3>
            <p>
              {scoped.blockStat
                ? `Status: ${scoped.blockStat.status} · ${scoped.blockStat.extracted}/${scoped.blockStat.identified} extracted`
                : "Not part of the latest extraction."}
            </p>
            {bankId && scoped.blockStat && (
              <BlockRetryCTA bankId={bankId} blockIdx={scoped.blockStat.excluded_block_index} />
            )}
          </div>
        ) : (
          scoped.sections.map((sec) => (
            <SectionBlock
              key={sec.section_ref}
              sec={sec}
              blocks={
                scoped.blockStat
                  ? [scoped.blockStat]
                  : blocksBySection[sec.section_ref] ?? []
              }
              v3Stat={v3StatsBySection[sec.section_ref] ?? null}
              bankId={bankId}
            />
          ))
        )}
      </>
    );
  }

  const total = detail.total_questions;
  const sectionsByRef: Record<string, typeof detail.sections[number]> = {};
  for (const s of detail.sections) sectionsByRef[s.section_ref] = s;

  // Book-wide pending-review count (sums pending items across all sections).
  // When > 0 we surface a "Mark all reviewed" bulk action so users don't
  // have to click Keep on each pending item individually.
  const totalPendingReview = detail.sections.reduce(
    (n, s) => n + (s.rejected?.length ?? 0),
    0,
  );

  // Build the render plan in this priority:
  //   1) Walk schemaOrder (hierarchical, with titles + depth)
  //   2) Append any detail.sections whose section_ref didn't appear in schema
  //      (excluded blocks like "Practice Questions" + back-compat for older banks)
  const renderPlan: { sectionRef: string; depth: number; title: string }[] = [];
  const seen = new Set<string>();
  for (const node of schemaOrder) {
    renderPlan.push(node);
    seen.add(node.sectionRef);
  }
  // Pass 1: find excluded sections that use "PARENT::CHILD" refs
  // and emit a synthetic parent header (depth 0) + each child at depth 1.
  // This makes practice sub-headings render as nested folders matching the PDF.
  const childGroups: Record<string, typeof detail.sections> = {};
  for (const sec of detail.sections) {
    if (seen.has(sec.section_ref)) continue;
    if (sec.section_ref.includes("::")) {
      const parent = sec.section_ref.split("::")[0];
      (childGroups[parent] = childGroups[parent] || []).push(sec);
    }
  }
  for (const sec of detail.sections) {
    if (seen.has(sec.section_ref)) continue;
    if (sec.section_ref.includes("::")) continue;
    // Standalone excluded section (no children) — render flat
    const kids = childGroups[sec.section_ref];
    renderPlan.push({
      sectionRef: sec.section_ref,
      depth: 0,
      title: sec.section_title || sec.section_ref,
    });
    seen.add(sec.section_ref);
    if (kids) {
      for (const c of kids) {
        const childTitle = c.section_ref.split("::").slice(1).join("::");
        renderPlan.push({
          sectionRef: c.section_ref,
          depth: 1,
          title: c.section_title || childTitle,
        });
        seen.add(c.section_ref);
      }
    }
  }
  // Any orphaned children whose parent never appeared as a section in the
  // detail payload (still emit a synthetic parent header so they nest).
  for (const [parent, kids] of Object.entries(childGroups)) {
    if (seen.has(parent)) continue;
    if (kids.every((k) => seen.has(k.section_ref))) continue;
    renderPlan.push({ sectionRef: parent, depth: 0, title: parent });
    seen.add(parent);
    for (const c of kids) {
      if (seen.has(c.section_ref)) continue;
      const childTitle = c.section_ref.split("::").slice(1).join("::");
      renderPlan.push({
        sectionRef: c.section_ref,
        depth: 1,
        title: c.section_title || childTitle,
      });
      seen.add(c.section_ref);
    }
  }
  // Final sweep — any straggler section that didn't fit either category
  for (const sec of detail.sections) {
    if (seen.has(sec.section_ref)) continue;
    renderPlan.push({
      sectionRef: sec.section_ref,
      depth: 0,
      title: sec.section_title || sec.section_ref,
    });
    seen.add(sec.section_ref);
  }

  // Only render schema nodes that have either questions OR a stats row
  // (status pill / retry button useful even if 0 extracted).
  // Synthetic parent headers for excluded sub-headings (e.g. "PRACTICE
  // QUESTIONS: CLASSROOM WING") have no direct questions — keep them if
  // any child under "<parent>::*" is renderable.
  const childrenByParent: Record<string, string[]> = {};
  for (const n of renderPlan) {
    if (n.sectionRef.includes("::")) {
      const p = n.sectionRef.split("::")[0];
      (childrenByParent[p] = childrenByParent[p] || []).push(n.sectionRef);
    }
  }
  const isRenderableLeaf = (ref: string) => {
    const sec = sectionsByRef[ref];
    const stat = v3StatsBySection[ref];
    return (sec && sec.questions.length > 0) || !!stat;
  };
  const renderable = renderPlan.filter((n) => {
    if (isRenderableLeaf(n.sectionRef)) return true;
    const kids = childrenByParent[n.sectionRef];
    if (kids && kids.some(isRenderableLeaf)) return true;
    return false;
  });

  const emptySchemaNodes = renderPlan.filter((n) => {
    const sec = sectionsByRef[n.sectionRef];
    const stat = v3StatsBySection[n.sectionRef];
    return !((sec && sec.questions.length > 0) || !!stat);
  });

  if (total === 0 && renderable.length === 0) {
    return (
      <div className="empty">
        <div className="empty-i">📝</div>
        <h3>No questions found</h3>
        <p>Gemini didn't find any questions printed in this book's sections.</p>
      </div>
    );
  }

  const populatedCount = renderable.filter(
    (n) => (sectionsByRef[n.sectionRef]?.questions.length ?? 0) > 0,
  ).length;

  return (
    <>
      <div className="cvh">
        <div className="cvt">
          {total} questions across {populatedCount} sections
          {detail.stats && detail.stats.missed > 0 && (() => {
            const subParts = aggregateSubPartFalsePositives(detail.sections);
            const adjMissed = Math.max(0, detail.stats.missed - subParts);
            return (
              <>
                {adjMissed > 0 && (
                  <span style={{ color: "var(--warn, #c80)", marginLeft: 8 }}>
                    · {adjMissed} missed
                  </span>
                )}
                {subParts > 0 && (
                  <span
                    style={{ color: "var(--text3)", marginLeft: 8, fontStyle: "italic" }}
                    title="Sub-parts like (i)(ii)(iii) under a single numbered question are kept as one row per the extractor spec."
                  >
                    · {subParts} sub-part{subParts === 1 ? "" : "s"}
                  </span>
                )}
              </>
            );
          })()}
          {totalPendingReview > 0 && bankId && (
            <span style={{ color: "var(--warn, #c80)", marginLeft: 8 }}>
              · {totalPendingReview} pending review
            </span>
          )}
        </div>
        {totalPendingReview > 0 && bankId && (
          <MarkAllReviewedButton
            bankId={bankId}
            count={totalPendingReview}
          />
        )}
      </div>

      {renderable.map((node) => {
        const sec = sectionsByRef[node.sectionRef] ?? ({
          section_ref: node.sectionRef,
          section_title: node.title,
          questions: [],
          by_kind: {},
          rejected: [],
          identified: 0,
          extracted: 0,
          missed: 0,
        } as unknown as typeof detail.sections[number]);
        return (
          <SectionBlock
            key={node.sectionRef}
            sec={sec}
            blocks={blocksBySection[node.sectionRef] ?? []}
            v3Stat={v3StatsBySection[node.sectionRef] ?? null}
            bankId={bankId}
            depth={node.depth}
            displayTitle={node.title}
          />
        );
      })}

      {emptySchemaNodes.length > 0 && (
        <div
          style={{
            marginTop: 24,
            fontSize: "0.7rem",
            color: "var(--text3)",
            fontStyle: "italic",
          }}
        >
          No questions in:{" "}
          {emptySchemaNodes.map((n) => n.title).join(", ")}
        </div>
      )}
    </>
  );
}

function EmptySectionRetryButton({
  bankId,
  sectionRef,
}: {
  bankId: UUID;
  sectionRef: string;
}) {
  const retry = useRetrySection();
  const [jobId, setJobId] = useState<UUID | null>(null);
  const { data: job } = useJob(jobId);
  useEffect(() => {
    if (job?.status === "succeeded" || job?.status === "failed") setJobId(null);
  }, [job?.status]);
  const busy = retry.isPending || !!jobId;
  return (
    <button
      className="btn primary"
      disabled={busy}
      style={{ marginTop: 16 }}
      onClick={() => {
        retry.mutate(
          { bankId, sectionRef },
          {
            onSuccess: (res) => {
              if (res?.job_id) setJobId(res.job_id);
            },
          },
        );
      }}
    >
      {busy ? "Retrying…" : "↺ Retry this section"}
    </button>
  );
}

function MarkAllReviewedButton({
  bankId,
  count,
}: {
  bankId: UUID;
  count: number;
}) {
  const restoreAll = useRestoreAllRejected();
  const busy = restoreAll.isPending;
  return (
    <button
      className="btn primary"
      disabled={busy}
      style={{ fontSize: "0.72rem", padding: "4px 12px" }}
      title={`Promote all ${count} pending items into the questions list`}
      onClick={() => {
        if (busy) return;
        restoreAll.mutate({ bankId });
      }}
    >
      {busy ? "Marking…" : `✓ Mark all reviewed (${count})`}
    </button>
  );
}

function BlockRetryCTA({ bankId, blockIdx }: { bankId: UUID; blockIdx: number }) {
  const reExtract = useReExtractBlock();
  return (
    <button
      className="btn primary"
      disabled={reExtract.isPending}
      style={{ marginTop: 16 }}
      onClick={() => reExtract.mutate({ bankId, blockIdx })}
    >
      {reExtract.isPending ? "Re-extracting…" : "↺ Re-extract this block"}
    </button>
  );
}

function SectionBlock({
  sec,
  blocks,
  v3Stat,
  bankId,
  depth = 0,
  displayTitle,
}: {
  sec: NonNullable<ReturnType<typeof useQuestions>["data"]>["sections"][number];
  blocks: NonNullable<NonNullable<ReturnType<typeof useQuestions>["data"]>["stats"]>["blocks"];
  v3Stat: ExtractionSectionStats | null;
  bankId: UUID | null;
  depth?: number;
  displayTitle?: string;
}) {
  const reExtract = useReExtractBlock();
  const retrySection = useRetrySection();
  const restoreRejected = useRestoreRejected();
  const discardRejected = useDiscardRejected();
  const hideQuestion = useHideQuestion();
  const unhideQuestion = useUnhideQuestion();
  const [pendingJobId, setPendingJobId] = useState<UUID | null>(null);
  const [showRejected, setShowRejected] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const pendingRejected = sec.rejected ?? [];
  const { data: pendingJob } = useJob(pendingJobId);
  useEffect(() => {
    if (pendingJob?.status === "succeeded" || pendingJob?.status === "failed") {
      setPendingJobId(null);
    }
  }, [pendingJob?.status]);

  const rawMissed = sec.missed ?? 0;
  const identified = sec.identified ?? sec.questions.length;
  const extracted = sec.extracted ?? sec.questions.length;
  // Subtract sub-part false positives — when a single extracted Q carries
  // (i)/(ii)/(iii) markers, the schema's expected count over-counted.
  const falsePositives = subPartFalsePositives(sec.questions, rawMissed);
  const missed = Math.max(0, rawMissed - falsePositives);
  const subPartHint = falsePositives > 0;
  const v3Status = v3Stat?.status ?? null;
  // E1 — Retry is ALWAYS available for any extracted section so users can
  // re-OCR the same pages on demand. Re-extraction wipes existing rows for
  // this (bank_id, section_ref) and re-runs the v3 extractor on the same slice.
  const showRetry = !!bankId && !!sec.section_ref;
  const rejectedItems = v3Stat?.rejected_items ?? [];
  const statusColor =
    v3Status === "complete"
      ? "var(--green, #2a9d5e)"
      : v3Status === "partial"
        ? "var(--warn, #c80)"
        : v3Status === "failed"
          ? "var(--red, #d33)"
          : "var(--text3)";

  const headingTitle = displayTitle ?? sec.section_title ?? sec.section_ref;
  // Depth-driven indent + size scaling so the Questions tree matches the
  // theory schema visual hierarchy.
  const indentPx = Math.min(depth, 4) * 18;
  const fontSize =
    depth === 0 ? "0.95rem" : depth === 1 ? "0.82rem" : "0.74rem";

  return (
    <div style={{ marginBottom: 24, marginLeft: indentPx }}>
      <h3
        style={{
          fontSize,
          fontWeight: depth === 0 ? 700 : 600,
          color: depth === 0 ? "var(--text1)" : "var(--text2)",
          marginBottom: 10,
          paddingBottom: 6,
          borderBottom: depth === 0 ? "1px solid var(--border)" : "1px dotted var(--border)",
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <span>{headingTitle}</span>
        <span style={{ color: "var(--text3)", fontWeight: 400, fontSize: "0.7rem" }}>
          · {extracted}/{identified} extracted
          {missed > 0 && (
            <span style={{ color: "var(--warn, #c80)", marginLeft: 4 }}>
              · {missed} missed
            </span>
          )}
          {subPartHint && (
            <span
              style={{ color: "var(--text3)", marginLeft: 4, fontStyle: "italic" }}
              title="Sub-parts like (i)(ii)(iii) under a single numbered question are kept as one row per the extractor spec."
            >
              · {falsePositives} sub-part{falsePositives === 1 ? "" : "s"}
            </span>
          )}
        </span>
        {v3Status && (
          <span
            style={{
              fontSize: "0.62rem",
              fontWeight: 600,
              padding: "1px 6px",
              borderRadius: 8,
              background: "var(--bg2, #f5f5fa)",
              color: statusColor,
              textTransform: "uppercase",
              letterSpacing: 0.4,
            }}
          >
            {v3Status}
          </span>
        )}
        {showRetry && (
          <button
            className="btn bg"
            disabled={retrySection.isPending || !!pendingJobId}
            style={{
              fontSize: "0.66rem",
              padding: "2px 8px",
              marginLeft: "auto",
              color: v3Status === "complete" ? "var(--text2)" : "var(--warn, #c80)",
              borderColor: v3Status === "complete" ? "var(--border)" : "var(--warn, #c80)",
            }}
            title="Re-OCR these pages and re-extract questions for this section"
            onClick={() => {
              if (!bankId) return;
              retrySection.mutate(
                { bankId, sectionRef: sec.section_ref },
                { onSuccess: (res) => setPendingJobId(res.job_id) },
              );
            }}
          >
            ↺ Retry section
          </button>
        )}
      </h3>

      {rejectedItems.length > 0 && (
        <div style={{ marginBottom: 10 }}>
          <button
            onClick={() => setShowRejected((v) => !v)}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--text3)",
              cursor: "pointer",
              fontSize: "0.68rem",
              padding: 0,
              textDecoration: "underline",
            }}
          >
            {showRejected ? "Hide" : "Show"} {rejectedItems.length} rejected
          </button>
          {showRejected && (
            <div
              style={{
                marginTop: 6,
                padding: 8,
                background: "var(--bg2, #f5f5fa)",
                borderRadius: 4,
                fontSize: "0.68rem",
                color: "var(--text2)",
              }}
            >
              {rejectedItems.map((it, i) => (
                <div
                  key={i}
                  style={{
                    padding: "4px 0",
                    borderTop: i === 0 ? "none" : "1px solid var(--border)",
                  }}
                >
                  <div style={{ color: "var(--warn, #c80)", fontFamily: "var(--mono)", fontSize: "0.62rem" }}>
                    {String(it._reject_reason ?? "rejected")}
                  </div>
                  <div style={{ whiteSpace: "pre-wrap", marginTop: 2 }}>
                    {String(it.raw_text ?? "")}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Per-block retry row — one button per excluded block that fed this section */}
      {blocks.length > 0 && bankId && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
          {blocks.map((b) => {
            const warn = b.status !== "ok" && b.status !== "empty";
            const busy = reExtract.isPending || !!pendingJobId;
            return (
              <button
                key={b.excluded_block_index}
                className="btn bg"
                disabled={busy}
                title={
                  `${b.title} · pages ${b.page_start ?? "?"}–${b.page_end ?? "?"}\n` +
                  `${b.extracted}/${b.identified} extracted (${b.status})\n` +
                  `link: ${b.link_method} (${Math.round(b.link_confidence * 100)}%)`
                }
                style={{
                  fontSize: "0.68rem",
                  padding: "3px 8px",
                  color: warn ? "var(--warn, #c80)" : "var(--text2)",
                  borderColor: warn ? "var(--warn, #c80)" : undefined,
                }}
                onClick={() => {
                  if (!bankId) return;
                  reExtract.mutate(
                    { bankId, blockIdx: b.excluded_block_index },
                    {
                      onSuccess: (res) => setPendingJobId(res.job_id),
                    },
                  );
                }}
              >
                ↺ {b.title}
                {warn ? ` · ${b.extracted}/${b.identified}` : ""}
              </button>
            );
          })}
        </div>
      )}

      {pendingJob && pendingJob.status === "running" && (
        <div style={{ fontSize: "0.7rem", color: "var(--text3)", marginBottom: 8 }}>
          Re-extracting… {pendingJob.message ?? ""}
        </div>
      )}

      {pendingRejected.length > 0 && bankId && (
        <div
          style={{
            marginBottom: 12,
            padding: 10,
            border: "1px dashed var(--warn, #c80)",
            borderRadius: 6,
            background: "rgba(204, 136, 0, 0.04)",
          }}
        >
          <div
            style={{
              fontSize: "0.7rem",
              color: "var(--warn, #c80)",
              fontWeight: 600,
              marginBottom: 6,
            }}
          >
            ⚠ {pendingRejected.length} item(s) pending review · keep or discard
          </div>
          {pendingRejected.map((r) => (
            <div
              key={r.id}
              className="card"
              style={{
                marginBottom: 6,
                padding: 8,
                background: "var(--bg1)",
              }}
            >
              <div
                style={{
                  fontSize: "0.62rem",
                  color: "var(--text3)",
                  fontFamily: "var(--mono)",
                  marginBottom: 4,
                }}
              >
                {r.reject_reason ?? "rejected"}
                {r.page_start ? ` · p.${r.page_start}` : ""}
              </div>
              <div
                style={{
                  whiteSpace: "pre-wrap",
                  fontSize: "0.78rem",
                  lineHeight: 1.5,
                  color: "var(--text2)",
                }}
              >
                {String(r.raw_text ?? "")}
              </div>
              <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                <button
                  className="btn primary"
                  style={{ fontSize: "0.66rem", padding: "2px 10px" }}
                  disabled={restoreRejected.isPending}
                  onClick={() =>
                    restoreRejected.mutate({ bankId, rejectedId: r.id })
                  }
                >
                  ✓ Keep
                </button>
                <button
                  className="btn bg"
                  style={{ fontSize: "0.66rem", padding: "2px 10px" }}
                  disabled={discardRejected.isPending}
                  onClick={() =>
                    discardRejected.mutate({ bankId, rejectedId: r.id })
                  }
                >
                  ✗ Discard
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {sec.questions.some((q) => q.is_hidden) && bankId && (
        <div style={{ marginBottom: 6 }}>
          <button
            onClick={() => setShowHidden((v) => !v)}
            style={{
              background: "transparent",
              border: "none",
              color: "var(--text3)",
              cursor: "pointer",
              fontSize: "0.66rem",
              padding: 0,
              textDecoration: "underline",
            }}
          >
            {showHidden ? "Hide" : "Show"}{" "}
            {sec.questions.filter((q) => q.is_hidden).length} hidden
          </button>
        </div>
      )}

      {sec.questions
        .filter((q) => showHidden || !q.is_hidden)
        .map((q, i) => (
        <div
          key={q.id}
          className="card"
          style={{
            marginBottom: 8,
            opacity: q.is_hidden ? 0.55 : 1,
            borderStyle: q.is_hidden ? "dashed" : undefined,
          }}
        >
          <div
            style={{
              fontSize: "0.64rem",
              color: "var(--text3)",
              marginBottom: 4,
              fontFamily: "var(--mono)",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <span>
              Q{q.question_number ?? i + 1}
              {q.exercise_ref ? ` · ${q.exercise_ref}` : ""}
              {q.page_start ? ` · p.${q.page_start}` : ""}
              {q.question_type ? ` · ${q.question_type}` : ""}
              {q.is_hidden ? " · hidden" : ""}
            </span>
            {bankId && (
              <button
                className="btn bg"
                style={{
                  marginLeft: "auto",
                  fontSize: "0.6rem",
                  padding: "1px 8px",
                  color: "var(--text3)",
                }}
                disabled={hideQuestion.isPending || unhideQuestion.isPending}
                onClick={() => {
                  if (q.is_hidden) {
                    unhideQuestion.mutate({ bankId, questionId: q.id });
                  } else {
                    hideQuestion.mutate({ bankId, questionId: q.id });
                  }
                }}
                title={q.is_hidden ? "Unhide" : "Hide from default view (kept in DB and exports)"}
              >
                {q.is_hidden ? "↺ Unhide" : "✕ Hide"}
              </button>
            )}
          </div>
          {(() => {
            // Phase 1 figure embedder: splice images at the touchpoint
            // (where "Figure X.Y" appears) — try body first, then solution,
            // then anything left renders as a trailing block.
            const figs = q.embedded_figures ?? [];
            const bodyRender = renderWithEmbeddedFigures(q.raw_text, figs);
            const remainAfterBody = figs.filter(
              (f) => !bodyRender.consumedIds.has(f.figure_id),
            );
            const solRender =
              q.has_solution && q.solution_text
                ? renderWithEmbeddedFigures(q.solution_text, remainAfterBody)
                : {
                    nodes: [] as (string | JSX.Element)[],
                    consumedIds: new Set<string>(),
                  };
            const trailing = remainAfterBody.filter(
              (f) => !solRender.consumedIds.has(f.figure_id),
            );
            return (
              <>
                <div
                  style={{
                    whiteSpace: "pre-wrap",
                    fontSize: "0.82rem",
                    lineHeight: 1.55,
                    color: "var(--text1)",
                  }}
                >
                  {bodyRender.nodes}
                </div>
                {trailing.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    {trailing.map((f) => (
                      <QuestionFigure key={f.figure_id} figure={f} />
                    ))}
                  </div>
                )}
                {q.has_solution && q.solution_text && (
                  <details style={{ marginTop: 8 }}>
                    <summary
                      style={{
                        fontSize: "0.68rem",
                        color: "var(--text3)",
                        cursor: "pointer",
                      }}
                    >
                      Solution
                    </summary>
                    <div
                      style={{
                        whiteSpace: "pre-wrap",
                        fontSize: "0.78rem",
                        lineHeight: 1.5,
                        color: "var(--text2)",
                        marginTop: 4,
                        paddingLeft: 8,
                        borderLeft: "2px solid var(--border)",
                      }}
                    >
                      {solRender.nodes}
                    </div>
                  </details>
                )}
              </>
            );
          })()}
        </div>
      ))}
    </div>
  );
}

// ─── Regeneration UI ──────────────────────────────────────────────────────

function statusColor(status: string): string {
  if (status === "ready") return "var(--accent)";
  if (status === "saved") return "var(--green, #2a9d5e)";
  if (status === "failed") return "var(--red, #d33)";
  return "var(--text3)";
}

function regenLabel(r: QuestionRegeneration, idx: number): string {
  return r.label || `Regen-${idx + 1}`;
}

function RegenRunBar({
  bankId,
  bookId,
  detail,
  activeRegenId,
  onSelectRegen,
}: {
  bankId: UUID;
  bookId: UUID;
  detail: NonNullable<ReturnType<typeof useQuestions>["data"]>;
  activeRegenId: UUID | null;
  onSelectRegen: (id: UUID | null) => void;
}) {
  const { data: regens } = useQuestionRegenerations(bookId, { pollMs: 2000 });
  const [showModal, setShowModal] = useState(false);

  // Simplified bar — show ONLY Original toggle + Regenerate button. Past
  // regens no longer accumulate as chips here; saved regens live in the
  // sidebar's "✨ Regenerated" folder, drafts disappear after Save/Discard.
  // The only mid-bar element is a single "Reviewing draft" pill when a
  // draft regen is currently selected.
  const activeRegen =
    activeRegenId
      ? (regens ?? []).find((r) => r.id === activeRegenId) ?? null
      : null;
  const isDraft = activeRegen && activeRegen.status !== "saved";

  return (
    <>
      <div
        style={{
          display: "flex",
          gap: 6,
          alignItems: "center",
          flexWrap: "wrap",
          marginBottom: 12,
          padding: "8px 10px",
          background: "var(--bg2, #f5f5fa)",
          borderRadius: 6,
          fontSize: "0.72rem",
        }}
      >
        <span style={{ color: "var(--text3)", marginRight: 4 }}>Run:</span>
        <button
          className="btn bg"
          onClick={() => onSelectRegen(null)}
          style={{
            fontSize: "0.7rem",
            padding: "3px 10px",
            background: !activeRegenId ? "var(--accent)" : undefined,
            color: !activeRegenId ? "white" : undefined,
            borderColor: !activeRegenId ? "var(--accent)" : undefined,
          }}
        >
          Original
        </button>
        {isDraft && activeRegen && (
          <span
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "3px 10px",
              borderRadius: 12,
              background: "var(--accent)",
              color: "white",
              fontSize: "0.66rem",
              fontWeight: 600,
            }}
            title={`Reviewing a draft regen (${activeRegen.status}). Approve in the panel below to save it to the Regenerated folder.`}
          >
            ✨ Reviewing draft · {activeRegen.question_count}q
          </span>
        )}
        <button
          className="btn primary"
          style={{ fontSize: "0.7rem", padding: "3px 10px", marginLeft: "auto" }}
          onClick={() => setShowModal(true)}
        >
          + Regenerate
        </button>
      </div>

      {showModal && (
        <RegenerateModal
          bankId={bankId}
          detail={detail}
          onClose={() => setShowModal(false)}
          onStarted={(id) => {
            setShowModal(false);
            onSelectRegen(id);
          }}
        />
      )}
    </>
  );
}

function RegenerateModal({
  bankId,
  detail,
  onClose,
  onStarted,
}: {
  bankId: UUID;
  detail: NonNullable<ReturnType<typeof useQuestions>["data"]>;
  onClose: () => void;
  onStarted: (id: UUID) => void;
}) {
  const [label, setLabel] = useState("");
  const [scope, setScope] = useState<"bank" | "sections">("bank");
  const [sectionRefs, setSectionRefs] = useState<string[]>([]);
  const [customInstructions, setCustomInstructions] = useState("");
  // R9 — v3 regen params
  const [similarityLevel, setSimilarityLevel] = useState<
    | "numbers_only"
    | "numbers_and_rephrase"
    | "new_question_same_topic"
    | "same_topic_add_one_concept"
    | "same_chapter_any_topic"
  >("numbers_and_rephrase");
  const [count, setCount] = useState<number>(3);
  const [questionType, setQuestionType] = useState<string>("same_as_source");
  const [priorityMode, setPriorityMode] = useState<
    "override" | "layer_on_top" | "specific_aspects"
  >("override");
  const start = useStartQuestionRegeneration();

  const allSections = detail.sections.map((s) => ({
    ref: s.section_ref,
    title: s.section_title,
  }));

  const submit = () => {
    if (scope === "sections" && sectionRefs.length === 0) return;
    start.mutate(
      {
        bankId,
        params: {
          scope,
          section_refs: scope === "sections" ? sectionRefs : null,
          custom_instructions: customInstructions.trim() || null,
          label: label.trim() || null,
          // R9 — v3 regen params
          similarity_level: similarityLevel,
          count: count,
          question_type: questionType,
          priority_mode: priorityMode,
        },
      },
      { onSuccess: (res) => onStarted(res.regen_id) },
    );
  };

  // R-polish: inline full-page panel (replaces the old popup modal) so the
  // parameter form sits in the main content area like the theory regen page.
  return (
    <div
      className="card"
      style={{
        background: "var(--bg1, white)",
        border: "1px solid var(--border, #e5e7eb)",
        padding: 24,
        borderRadius: 10,
        margin: "8px 0 16px 0",
        maxWidth: 920,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          marginBottom: 16,
          gap: 12,
        }}
      >
        <div>
          <div style={{ fontSize: "0.62rem", color: "var(--text3)", letterSpacing: 0.5 }}>
            START REGENERATION
          </div>
          <h2 style={{ margin: "2px 0 4px 0", fontSize: "1rem", fontWeight: 700 }}>
            Regenerate questions
          </h2>
          <p style={{ fontSize: "0.72rem", color: "var(--text3)", margin: 0 }}>
            Re-generates questions from the existing extracted bank using Gemini.
            Originals stay intact. Custom instruction honours the priority mode below.
          </p>
        </div>
        <button
          className="btn bg"
          onClick={onClose}
          disabled={start.isPending}
          style={{ fontSize: "0.72rem", padding: "4px 10px" }}
          aria-label="Close"
        >
          ✕ Close
        </button>
      </div>

        <label style={{ display: "block", marginBottom: 12 }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>Label (optional)</span>
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. v2 — stricter math"
            style={{
              width: "100%",
              padding: "6px 8px",
              fontSize: "0.78rem",
              marginTop: 4,
              border: "1px solid var(--border)",
              borderRadius: 4,
              background: "var(--bg1)",
              color: "var(--text1)",
            }}
            maxLength={64}
          />
        </label>

        <div style={{ marginBottom: 12 }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>Scope</span>
          <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
            <button
              className="btn bg"
              onClick={() => setScope("bank")}
              style={{
                fontSize: "0.72rem",
                padding: "5px 12px",
                background: scope === "bank" ? "var(--accent)" : undefined,
                color: scope === "bank" ? "white" : undefined,
                borderColor: scope === "bank" ? "var(--accent)" : undefined,
              }}
            >
              Whole bank
            </button>
            <button
              className="btn bg"
              onClick={() => setScope("sections")}
              style={{
                fontSize: "0.72rem",
                padding: "5px 12px",
                background: scope === "sections" ? "var(--accent)" : undefined,
                color: scope === "sections" ? "white" : undefined,
                borderColor: scope === "sections" ? "var(--accent)" : undefined,
              }}
            >
              Specific sections
            </button>
          </div>
        </div>

        {scope === "sections" && (
          <div style={{ marginBottom: 12 }}>
            <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>
              Sections ({sectionRefs.length} selected)
            </span>
            <div
              style={{
                marginTop: 4,
                maxHeight: 180,
                overflow: "auto",
                border: "1px solid var(--border)",
                borderRadius: 4,
                padding: 6,
                background: "var(--bg1)",
              }}
            >
              {allSections.map((s) => {
                const checked = sectionRefs.includes(s.ref);
                return (
                  <label
                    key={s.ref}
                    style={{
                      display: "flex",
                      gap: 6,
                      padding: "3px 4px",
                      fontSize: "0.72rem",
                      cursor: "pointer",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => {
                        setSectionRefs((prev) =>
                          checked ? prev.filter((x) => x !== s.ref) : [...prev, s.ref],
                        );
                      }}
                    />
                    <span>
                      §{s.ref} {s.title}
                    </span>
                  </label>
                );
              })}
            </div>
          </div>
        )}

        {/* R9 — v3 regen params (similarity / count / question type) */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 12,
            marginBottom: 12,
          }}
        >
          <label style={{ display: "block" }}>
            <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>Similarity level</span>
            <select
              value={similarityLevel}
              onChange={(e) =>
                setSimilarityLevel(e.target.value as typeof similarityLevel)
              }
              style={{
                width: "100%",
                padding: "6px 8px",
                fontSize: "0.78rem",
                marginTop: 4,
                border: "1px solid var(--border)",
                borderRadius: 4,
                background: "var(--bg1)",
                color: "var(--text1)",
              }}
            >
              <option value="numbers_only">Numbers only</option>
              <option value="numbers_and_rephrase">Numbers + rephrase</option>
              <option value="new_question_same_topic">New Q, same topic</option>
              <option value="same_topic_add_one_concept">Same topic + 1 concept</option>
              <option value="same_chapter_any_topic">Same chapter, any topic</option>
            </select>
          </label>
          <label style={{ display: "block" }}>
            <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>Variants per source (1–20)</span>
            <input
              type="number"
              min={1}
              max={20}
              value={count}
              onChange={(e) => {
                const n = parseInt(e.target.value || "0", 10);
                if (!Number.isNaN(n) && n >= 1 && n <= 20) setCount(n);
              }}
              style={{
                width: "100%",
                padding: "6px 8px",
                fontSize: "0.78rem",
                marginTop: 4,
                border: "1px solid var(--border)",
                borderRadius: 4,
                background: "var(--bg1)",
                color: "var(--text1)",
              }}
            />
          </label>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 12,
            marginBottom: 12,
          }}
        >
          <label style={{ display: "block" }}>
            <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>Output question type</span>
            <select
              value={questionType}
              onChange={(e) => setQuestionType(e.target.value)}
              style={{
                width: "100%",
                padding: "6px 8px",
                fontSize: "0.78rem",
                marginTop: 4,
                border: "1px solid var(--border)",
                borderRadius: 4,
                background: "var(--bg1)",
                color: "var(--text1)",
              }}
            >
              <option value="same_as_source">Same as source</option>
              <option value="Objective_SCQ">Objective SCQ</option>
              <option value="Objective_MCQ">Objective MCQ</option>
              <option value="Binary_TF">Binary True/False</option>
              <option value="Integer_SingleDigit">Integer (single digit)</option>
              <option value="Integer_DoubleDigit">Integer (double digit)</option>
              <option value="Integer_NDigits">Integer (N digits)</option>
              <option value="Numerical_2Decimal">Numerical (2 decimal)</option>
              <option value="Matching_RadioGrid">Matching</option>
              <option value="Subjective_VeryShort">Subjective (very short)</option>
              <option value="Subjective_Short">Subjective (short)</option>
              <option value="Subjective_Long">Subjective (long)</option>
              <option value="Comprehension">Comprehension</option>
              <option value="AssertionReasoning">Assertion-Reasoning</option>
              <option value="FillInTheBlanks">Fill in the blanks</option>
            </select>
          </label>
          <label style={{ display: "block" }}>
            <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>Priority mode (when custom instruction set)</span>
            <select
              value={priorityMode}
              onChange={(e) =>
                setPriorityMode(e.target.value as typeof priorityMode)
              }
              style={{
                width: "100%",
                padding: "6px 8px",
                fontSize: "0.78rem",
                marginTop: 4,
                border: "1px solid var(--border)",
                borderRadius: 4,
                background: "var(--bg1)",
                color: "var(--text1)",
              }}
            >
              <option value="override">Override — replace default behavior</option>
              <option value="layer_on_top">Layer on top — default + custom</option>
              <option value="specific_aspects">Specific aspects only</option>
            </select>
          </label>
        </div>

        <label style={{ display: "block", marginBottom: 16 }}>
          <span style={{ fontSize: "0.7rem", color: "var(--text2)" }}>
            Custom instructions (applied per priority mode above)
          </span>
          <textarea
            value={customInstructions}
            onChange={(e) => setCustomInstructions(e.target.value)}
            placeholder="e.g. Translate to Hindi · Use Indian Rupee for currency · Make harder"
            rows={4}
            style={{
              width: "100%",
              padding: "6px 8px",
              fontSize: "0.78rem",
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

        {start.isError && (
          <div style={{ color: "var(--red)", fontSize: "0.72rem", marginBottom: 10 }}>
            {(start.error as Error).message}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button className="btn bg" onClick={onClose} disabled={start.isPending}>
            Cancel
          </button>
          <button
            className="btn primary"
            onClick={submit}
            disabled={start.isPending || (scope === "sections" && sectionRefs.length === 0)}
          >
            {start.isPending ? "Starting..." : "Start regeneration"}
          </button>
        </div>
      </div>
  );
}

// Read-only params card shown at the top of the regen review pane so the
// reviewer always sees which knobs produced these variants. Includes a
// "+ New regen with same params" pre-fill button (sets the start dialog).
function RegenParamsCard({ regen }: { regen: QuestionRegeneration }) {
  type RegenWithParams = QuestionRegeneration & {
    similarity_level?: string | null;
    count?: number | null;
    question_type?: string | null;
    priority_mode?: string | null;
  };
  const r = regen as RegenWithParams;
  const items: [string, string][] = [
    ["Similarity", r.similarity_level ?? "—"],
    ["Variants per source", String(r.count ?? "—")],
    ["Question type", r.question_type ?? "same_as_source"],
    ["Priority mode", r.priority_mode ?? "—"],
  ];
  return (
    <div
      style={{
        marginBottom: 12,
        padding: "8px 12px",
        border: "1px solid var(--border)",
        borderRadius: 6,
        background: "var(--bg2, #f5f5fa)",
        fontSize: "0.7rem",
      }}
    >
      <div
        style={{
          fontSize: "0.6rem",
          textTransform: "uppercase",
          letterSpacing: 0.5,
          color: "var(--text3)",
          marginBottom: 6,
        }}
      >
        Parameters used for this run
      </div>
      <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
        {items.map(([k, v]) => (
          <div key={k}>
            <span style={{ color: "var(--text3)" }}>{k}:</span>{" "}
            <span style={{ fontWeight: 600 }}>{v}</span>
          </div>
        ))}
      </div>
      {regen.custom_instructions && (
        <div
          style={{
            marginTop: 8,
            paddingTop: 8,
            borderTop: "1px solid var(--border)",
            fontStyle: "italic",
            color: "var(--text2)",
          }}
        >
          <span style={{ color: "var(--text3)", fontStyle: "normal" }}>
            Custom:
          </span>{" "}
          ⚡ {regen.custom_instructions}
        </div>
      )}
    </div>
  );
}




function RegenView({
  regenId,
  bookId,
  originalDetail,
}: {
  regenId: UUID;
  bookId: UUID;
  originalDetail: NonNullable<ReturnType<typeof useQuestions>["data"]> | null;
}) {
  const { data: regen } = useQuestionRegen(regenId, { pollMs: 2000 });
  const isExtracting = regen?.status === "pending" || regen?.status === "extracting";
  const { data: regenData, refetch: refetchRegenData } = useRegenQuestions(regenId, {
    pollMs: isExtracting ? 2500 : undefined,
  });
  const { data: regenJob } = useJob(regen?.job_id ?? null, { pollMs: 1000 });

  // When the regen status flips from extracting → ready/partial/saved,
  // the regenData hook stops polling, but its last cached snapshot may
  // have been taken mid-flight when the questions hadn't been persisted
  // yet (showing "No questions in this regeneration yet"). Force one
  // refetch on the settle transition so the user sees the final rows.
  useEffect(() => {
    if (
      regen?.status === "ready"
      || regen?.status === "partial"
      || regen?.status === "saved"
    ) {
      void refetchRegenData();
    }
  }, [regen?.status, refetchRegenData]);

  const saveRegen = useSaveQuestionRegeneration();
  const deleteRegen = useDeleteQuestionRegeneration();
  const bulkDelete = useBulkDeleteRegenQuestions();
  const retrySection = useRetryRegenSection();
  const startRegen = useStartQuestionRegeneration();
  const { selectQuestionRegen } = useUI();
  // Inline custom instructions — lets the reviewer tweak instructions and
  // re-run THIS regen without leaving the page (no modal). Seeded from
  // the current regen's instructions so the user can edit-and-rerun.
  const [inlineInstructions, setInlineInstructions] = useState<string>("");
  useEffect(() => {
    if (regen?.custom_instructions != null) {
      setInlineInstructions(regen.custom_instructions);
    }
  }, [regen?.custom_instructions]);

  // Theory-regen-style review state — left section list + right pane.
  const [activeSectionId, setActiveSectionId] = useState<string | null>(null);
  // Computed active section ref — used by header buttons (Retry section)
  // and the right-pane title. Falls back to the first section so header
  // buttons stay enabled when the user hasn't clicked anything yet.
  const _firstSectionRef =
    regenData?.sections.find((s) => !!s.section_ref)?.section_ref ?? null;
  const activeRefForHeader = activeSectionId ?? _firstSectionRef;
  const retryingHeaderActive =
    activeRefForHeader !== null &&
    retrySection.isPending &&
    retrySection.variables?.sectionRef === activeRefForHeader;
  // Per-question approve tracking (UI-local; "rejected" deletes the row).
  // Per-question approval state removed — the model is now: reject (×)
  // the variants you don't want, then Approve & save the run; whatever's
  // left in the regen at save-time is what lands in the Regenerated
  // folder. Total kept variants is the live row count from regenData.
  const totalKept = (regenData?.sections ?? []).reduce(
    (n, s) => n + s.questions.length,
    0,
  );

  const originalsBySection = useMemo(() => {
    const m: Record<string, Question[]> = {};
    for (const sec of originalDetail?.sections ?? []) {
      m[sec.section_ref] = sec.questions;
    }
    return m;
  }, [originalDetail]);

  if (!regen) {
    return <div style={{ color: "var(--text3)" }}>Loading regen…</div>;
  }

  return (
    <>
      <div
        className="card"
        style={{
          marginBottom: 12,
          borderLeft: `3px solid ${statusColor(regen.status)}`,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            flexWrap: "wrap",
          }}
        >
          <div>
            <div style={{ fontSize: "0.62rem", color: "var(--text3)" }}>REGENERATION</div>
            <div style={{ fontSize: "0.84rem", fontWeight: 600 }}>
              {regen.label || `Regen run`}
              <span style={{ color: "var(--text3)", fontWeight: 400, marginLeft: 8, fontSize: "0.7rem" }}>
                · {regen.scope}
                {regen.scope === "sections" && regen.section_refs.length > 0
                  ? ` · ${regen.section_refs.join(", ")}`
                  : ""}
                {" · "}
                <span style={{ color: statusColor(regen.status) }}>{regen.status}</span>
                {" · "}
                {regen.question_count} questions
              </span>
            </div>
            {regen.custom_instructions && (
              <div
                style={{
                  fontSize: "0.7rem",
                  color: "var(--text2)",
                  marginTop: 4,
                  fontStyle: "italic",
                  maxWidth: 600,
                }}
                title={regen.custom_instructions}
              >
                ⚡ {regen.custom_instructions}
              </div>
            )}
          </div>

          <div style={{ display: "flex", gap: 6, marginLeft: "auto", flexWrap: "wrap" }}>
            {/* R10 — whole-regen exports */}
            {(regen.status === "ready" || regen.status === "saved" || regen.status === "partial") && (
              <>
                <button
                  className="btn bg"
                  style={{ fontSize: "0.72rem", padding: "4px 10px" }}
                  title="Download whole regen as JSON"
                  onClick={() => api.exportRegenJson(regenId)}
                >
                  ⬇ .json
                </button>
                <button
                  className="btn bg"
                  style={{ fontSize: "0.72rem", padding: "4px 10px" }}
                  title="Download whole regen as Markdown"
                  onClick={() => api.exportRegenMarkdown(regenId)}
                >
                  ⬇ .md
                </button>
                <button
                  className="btn bg"
                  style={{ fontSize: "0.72rem", padding: "4px 10px" }}
                  title="Download whole regen as Word document"
                  onClick={() => api.exportRegenDocx(regenId)}
                >
                  ⬇ .docx
                </button>
              </>
            )}
            {/* R2 — allow save for ready OR partial; partial means some
                 sections failed/skipped but the user can still keep what
                 worked. Saved runs surface in the "Regenerated" folder. */}
            {(regen.status === "ready" || regen.status === "partial") && (
              <button
                className="btn primary"
                style={{ fontSize: "0.72rem", padding: "4px 10px" }}
                disabled={saveRegen.isPending}
                title={
                  regen.status === "partial"
                    ? "Save this run with its current sections (some sections did not complete)"
                    : "Save this regeneration into the Regenerated folder"
                }
                onClick={() =>
                  saveRegen.mutate(
                    { regenId, bookId },
                    {
                      // After approve: deselect so user pops back to Original
                      // view; the regen now appears under the ✨ Regenerated
                      // sidebar folder, clickable from there.
                      onSuccess: () => selectQuestionRegen(null),
                    },
                  )
                }
              >
                {saveRegen.isPending ? "Approving…" : "✓ Approve & move to Regenerated"}
              </button>
            )}
            {/* R4 — Re-run with new params: clears active regen and shows
                 the RegenRunBar so user can tweak inputs and start fresh. */}
            {(regen.status === "ready"
              || regen.status === "saved"
              || regen.status === "partial"
              || regen.status === "failed") && (
              <button
                className="btn bg"
                style={{ fontSize: "0.72rem", padding: "4px 10px" }}
                title="Go back and re-run with different parameters / custom instructions"
                onClick={() => selectQuestionRegen(null)}
              >
                ↻ Re-run with new params
              </button>
            )}
            {/* Per-active-section Retry — moved up here from the section row
                 so all actions sit together. Operates on the section the
                 user is currently viewing in the right pane. */}
            {activeRefForHeader
              && (regen.status === "ready" || regen.status === "partial") && (
              <button
                className="btn bg"
                style={{ fontSize: "0.72rem", padding: "4px 10px" }}
                title={`Re-run regen for ONLY this section (${activeRefForHeader})`}
                disabled={retryingHeaderActive}
                onClick={() => {
                  if (!activeRefForHeader) return;
                  if (!confirm(`Re-run regeneration for this section? Existing regen questions for it will be replaced.`)) return;
                  retrySection.mutate({ regenId, sectionRef: activeRefForHeader });
                }}
              >
                {retryingHeaderActive ? "Retrying…" : "🔁 Retry this section"}
              </button>
            )}
            <button
              className="btn bg"
              style={{
                fontSize: "0.72rem",
                padding: "4px 10px",
                color: "var(--red, #d33)",
              }}
              disabled={deleteRegen.isPending}
              onClick={() => {
                if (!confirm("Delete this regeneration run? Originals are unaffected.")) return;
                deleteRegen.mutate(
                  { regenId, bookId },
                  {
                    onSuccess: () => selectQuestionRegen(null),
                  },
                );
              }}
            >
              🗑 Delete run
            </button>
          </div>
        </div>

        {/* R5 — summary table removed from regen review page (kept on
             the bank page). The diff layout below is the primary view. */}

        {isExtracting && (
          <div style={{ marginTop: 10 }}>
            <div className="prog">
              <div
                className="progb"
                style={{
                  width: `${Math.max(2, regenJob?.progress ?? 0)}%`,
                  transition: "width 0.4s ease",
                }}
              />
            </div>
            <div style={{ fontSize: "0.7rem", color: "var(--text3)", marginTop: 4 }}>
              {regenJob?.message ?? "Extracting…"}{" "}
              <span style={{ fontFamily: "var(--mono)" }}>{regenJob?.progress ?? 0}%</span>
            </div>
          </div>
        )}

        {regen.status === "failed" && regen.last_error && (
          <div style={{ color: "var(--red)", fontSize: "0.72rem", marginTop: 8 }}>
            {regen.last_error}
          </div>
        )}
      </div>

      {regenData && regenData.sections.length === 0 && !isExtracting && (
        <div className="empty" style={{ padding: 30 }}>
          <div className="empty-i">📝</div>
          <h3>No questions in this regeneration yet</h3>
        </div>
      )}

      {/* Theory-style review layout: left section list + right active pane */}
      {regenData && regenData.sections.length > 0 && (() => {
        const sectionRefs = regenData.sections
          .map((s) => s.section_ref ?? "")
          .filter(Boolean);
        const activeRef = activeSectionId ?? sectionRefs[0] ?? null;
        const activeSec = regenData.sections.find((s) => (s.section_ref ?? "") === activeRef) || null;
        const activeOriginals = activeRef ? (originalsBySection[activeRef] ?? []) : [];
        const retryingActive =
          activeRef !== null
          && retrySection.isPending
          && retrySection.variables?.sectionRef === activeRef;
        return (
          <div
            style={{
              display: "flex",
              gap: 0,
              border: "1px solid var(--border)",
              borderRadius: 8,
              overflow: "hidden",
              minHeight: "calc(100vh - 280px)",
            }}
          >
            {/* Left: section list */}
            <div
              style={{
                width: 240,
                flexShrink: 0,
                borderRight: "1px solid var(--border)",
                background: "var(--bg2, #f5f5fa)",
                display: "flex",
                flexDirection: "column",
              }}
            >
              <div
                style={{
                  padding: "10px 12px",
                  borderBottom: "1px solid var(--border)",
                  fontSize: "0.62rem",
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: 0.5,
                  color: "var(--text3)",
                }}
              >
                Sections · {sectionRefs.length}
              </div>
              <div style={{ flex: 1, overflowY: "auto", padding: "4px 0" }}>
                {regenData.sections.map((sec) => {
                  const ref = sec.section_ref ?? "";
                  const isActive = ref === activeRef;
                  const total = sec.questions.length;
                  // Section indicator: ✓ has kept variants, — none kept.
                  const icon = total === 0 ? "—" : "✓";
                  return (
                    <button
                      key={ref || "_unsec"}
                      onClick={() => setActiveSectionId(ref)}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        width: "calc(100% - 8px)",
                        margin: "1px 4px",
                        padding: "6px 10px",
                        borderRadius: 6,
                        border: "1px solid",
                        borderColor: isActive ? "var(--accent)" : "transparent",
                        background: isActive ? "var(--bg1, white)" : "transparent",
                        cursor: "pointer",
                        textAlign: "left",
                        font: "inherit",
                      }}
                    >
                      <span
                        style={{
                          fontSize: "0.85rem",
                          color: total > 0 ? "var(--green, #2a9d5e)" : "var(--text3)",
                        }}
                      >
                        {icon}
                      </span>
                      <span
                        style={{
                          fontSize: "0.7rem",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          color: isActive ? "var(--text1)" : "var(--text2)",
                          fontWeight: isActive ? 600 : 400,
                          flex: 1,
                        }}
                      >
                        {ref || "Unsectioned"}
                      </span>
                      <span
                        style={{
                          fontSize: "0.6rem",
                          color: "var(--text3)",
                          fontVariantNumeric: "tabular-nums",
                        }}
                      >
                        {total} kept
                      </span>
                    </button>
                  );
                })}
              </div>
              {(regen.status === "ready" || regen.status === "partial") && (
                <div style={{ padding: "8px 10px", borderTop: "1px solid var(--border)" }}>
                  <button
                    className="btn primary"
                    style={{ width: "100%", fontSize: "0.7rem", padding: "6px 8px" }}
                    disabled={saveRegen.isPending || totalKept === 0}
                    onClick={() =>
                      saveRegen.mutate(
                        { regenId, bookId },
                        { onSuccess: () => selectQuestionRegen(null) },
                      )
                    }
                    title={
                      totalKept === 0
                        ? "All variants rejected — nothing left to save"
                        : "Approve & move all kept variants to the ✨ Regenerated folder"
                    }
                  >
                    {saveRegen.isPending
                      ? "Approving…"
                      : `✓ Approve & move ${totalKept} to Regenerated`}
                  </button>
                </div>
              )}
            </div>

            {/* Right: active section's side-by-side review */}
            <div style={{ flex: 1, display: "flex", flexDirection: "column", padding: 12, overflow: "auto" }}>
              {!activeSec ? (
                <div className="empty" style={{ margin: "auto" }}>
                  <div className="empty-i">🔍</div>
                  <h3>Pick a section</h3>
                </div>
              ) : (
                <>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      marginBottom: 10,
                      paddingBottom: 8,
                      borderBottom: "1px solid var(--border)",
                      flexWrap: "wrap",
                    }}
                  >
                    {/* Compact section title — full ref already visible via
                         the sidebar / section list; here just the human
                         name + counts. Hover shows the full ref for debug. */}
                    <h3
                      style={{ fontSize: "0.84rem", fontWeight: 700, margin: 0, flex: "1 1 auto" }}
                      title={activeRef ?? ""}
                    >
                      {activeSec.section_title || activeRef || "Unsectioned"}
                      <span style={{ color: "var(--text3)", fontWeight: 400, fontSize: "0.7rem", marginLeft: 8 }}>
                        · {activeOriginals.length} original · {activeSec.questions.length} regen
                      </span>
                    </h3>
                    {/* All section-scoped actions live in the regen header
                         card above (Retry / Re-run / Approve / Delete). */}
                  </div>

                  {/* Params card — read-only summary of the regen settings */}
                  <RegenParamsCard regen={regen} />

                  {/* Inline custom-instructions + quick re-run (draft only).
                       Lets the user tweak instructions and start a NEW regen
                       with the SAME params + new instructions, without
                       leaving the page. Saved runs hide this — they are
                       final. */}
                  {(regen.status === "ready" || regen.status === "partial") && (
                    <div
                      style={{
                        marginBottom: 12,
                        padding: 10,
                        border: "1px solid var(--border)",
                        borderRadius: 6,
                        background: "var(--bg1)",
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          alignItems: "flex-start",
                          gap: 10,
                          flexWrap: "wrap",
                        }}
                      >
                        <textarea
                          value={inlineInstructions}
                          onChange={(e) => setInlineInstructions(e.target.value)}
                          placeholder="Custom instructions for the next run (e.g. translate to Hindi, make harder, add a hint)"
                          rows={2}
                          style={{
                            flex: "1 1 360px",
                            padding: "6px 8px",
                            fontSize: "0.74rem",
                            border: "1px solid var(--border)",
                            borderRadius: 4,
                            fontFamily: "inherit",
                            resize: "vertical",
                            background: "var(--bg1)",
                            color: "var(--text1)",
                          }}
                        />
                        <button
                          className="btn primary"
                          style={{ fontSize: "0.72rem", padding: "6px 12px" }}
                          disabled={startRegen.isPending}
                          title="Start a new regen with the same params + these instructions"
                          onClick={() => {
                            startRegen.mutate(
                              {
                                bankId: regen.bank_id,
                                params: {
                                  scope: regen.scope as "bank" | "sections",
                                  section_refs:
                                    regen.scope === "sections"
                                      ? regen.section_refs
                                      : null,
                                  custom_instructions: inlineInstructions.trim() || null,
                                  // Mirror params from the current run.
                                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                                  similarity_level: (regen as any).similarity_level ?? null,
                                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                                  count: (regen as any).count ?? null,
                                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                                  question_type: (regen as any).question_type ?? null,
                                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                                  priority_mode: (regen as any).priority_mode ?? null,
                                },
                              },
                              { onSuccess: (res) => selectQuestionRegen(res.regen_id) },
                            );
                          }}
                        >
                          {startRegen.isPending ? "Starting…" : "↻ Re-run with these"}
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Once approved/saved → show the regen questions in a
                      single-column layout (same shape as the Original
                      section view). The side-by-side diff is only for
                      DRAFTS (ready/partial), where the user is reviewing
                      what to keep before approving. */}
                  {regen.status === "saved" ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                      {activeSec.questions.length === 0 && (
                        <div style={{ fontSize: "0.7rem", color: "var(--text3)", fontStyle: "italic" }}>
                          No regenerated questions in this section.
                        </div>
                      )}
                      {activeSec.questions.map((q, i) => (
                        <QuestionCard key={q.id} q={q} index={i} />
                      ))}
                    </div>
                  ) : activeSec.sources && activeSec.sources.length > 0 ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                      {activeSec.sources.map((grp, gi) => (
                        <div
                          key={grp.source_id ?? `_orphan_${gi}`}
                          style={{
                            display: "grid",
                            gridTemplateColumns: "1fr 1fr",
                            gap: 12,
                            border: "1px solid var(--border)",
                            borderRadius: 6,
                            padding: 10,
                            background: "var(--bg2, #f5f5fa)",
                          }}
                        >
                          <div>
                            <div className="clbl" style={{ marginBottom: 6 }}>
                              {grp.source ? "Original" : "Orphan variants (no linked source)"}
                            </div>
                            {grp.source ? (
                              <QuestionCard q={grp.source} index={0} />
                            ) : (
                              <div style={{ fontSize: "0.7rem", color: "var(--text3)", fontStyle: "italic" }}>
                                These variants pre-date the source-linking column.
                              </div>
                            )}
                          </div>
                          <div>
                            <div className="clbl" style={{ marginBottom: 6 }}>
                              Variants ({grp.variants.length})
                            </div>
                            {grp.variants.map((q, i) => (
                              <div key={q.id} style={{ marginBottom: 8 }}>
                                <QuestionCard
                                  q={q}
                                  index={i}
                                  onDelete={() => {
                                    if (!confirm("Reject this regenerated question? It will be deleted from this run.")) return;
                                    bulkDelete.mutate({ regenId, questionIds: [q.id] });
                                  }}
                                />
                              </div>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    <div>
                      <div className="clbl" style={{ marginBottom: 6 }}>Original</div>
                      {activeOriginals.length === 0 && (
                        <div style={{ fontSize: "0.7rem", color: "var(--text3)", fontStyle: "italic" }}>
                          No originals in this section.
                        </div>
                      )}
                      {activeOriginals.map((q, i) => (
                        <QuestionCard key={q.id} q={q} index={i} />
                      ))}
                    </div>
                    <div>
                      <div className="clbl" style={{ marginBottom: 6 }}>Regen</div>
                      {activeSec.questions.length === 0 && (
                        <div style={{ fontSize: "0.7rem", color: "var(--text3)", fontStyle: "italic" }}>
                          No regen questions in this section.
                        </div>
                      )}
                      {activeSec.questions.map((q, i) => (
                        <div key={q.id} style={{ marginBottom: 8 }}>
                          <QuestionCard
                            q={q}
                            index={i}
                            onDelete={() => {
                              if (!confirm("Reject this regenerated question? It will be deleted from this run.")) return;
                              bulkDelete.mutate({ regenId, questionIds: [q.id] });
                            }}
                          />
                        </div>
                      ))}
                    </div>
                  </div>
                  )}
                </>
              )}
            </div>
          </div>
        );
      })()}
    </>
  );
}

function QuestionCard({
  q,
  index,
  onDelete,
}: {
  q: Question;
  index: number;
  onDelete?: () => void;
}) {
  return (
    <div className="card" style={{ marginBottom: 8, position: "relative" }}>
      <div
        style={{
          fontSize: "0.62rem",
          color: "var(--text3)",
          marginBottom: 4,
          fontFamily: "var(--mono)",
          display: "flex",
          gap: 6,
          alignItems: "center",
        }}
      >
        <span>
          Q{q.question_number ?? index + 1}
          {q.exercise_ref ? ` · ${q.exercise_ref}` : ""}
          {q.page_start ? ` · p.${q.page_start}` : ""}
          {q.question_type ? ` · ${q.question_type}` : ""}
        </span>
        {onDelete && (
          <button
            onClick={onDelete}
            title="Delete this regen question"
            style={{
              marginLeft: "auto",
              background: "transparent",
              border: "none",
              color: "var(--text3)",
              cursor: "pointer",
              fontSize: "0.78rem",
              padding: "0 4px",
            }}
          >
            ✕
          </button>
        )}
      </div>
      {(() => {
        // Splice embedded figures at the touchpoint in body / solution,
        // trailing for anything still unmatched. Same logic as SectionBlock.
        const figs = q.embedded_figures ?? [];
        const bodyRender = renderWithEmbeddedFigures(q.raw_text, figs);
        const remainAfterBody = figs.filter(
          (f) => !bodyRender.consumedIds.has(f.figure_id),
        );
        const solRender =
          q.has_solution && q.solution_text
            ? renderWithEmbeddedFigures(q.solution_text, remainAfterBody)
            : {
                nodes: [] as (string | JSX.Element)[],
                consumedIds: new Set<string>(),
              };
        const trailing = remainAfterBody.filter(
          (f) => !solRender.consumedIds.has(f.figure_id),
        );
        return (
          <>
            <div
              style={{
                whiteSpace: "pre-wrap",
                fontSize: "0.78rem",
                lineHeight: 1.5,
                color: "var(--text1)",
              }}
            >
              {bodyRender.nodes}
            </div>
            {trailing.length > 0 && (
              <div style={{ marginTop: 8 }}>
                {trailing.map((f) => (
                  <QuestionFigure key={f.figure_id} figure={f} />
                ))}
              </div>
            )}
            {q.has_solution && q.solution_text && (
              <details style={{ marginTop: 6 }}>
                <summary
                  style={{
                    fontSize: "0.66rem",
                    color: "var(--text3)",
                    cursor: "pointer",
                  }}
                >
                  Solution
                </summary>
                <div
                  style={{
                    whiteSpace: "pre-wrap",
                    fontSize: "0.74rem",
                    lineHeight: 1.45,
                    color: "var(--text2)",
                    marginTop: 4,
                    paddingLeft: 8,
                    borderLeft: "2px solid var(--border)",
                  }}
                >
                  {solRender.nodes}
                </div>
              </details>
            )}
          </>
        );
      })()}
    </div>
  );
}

// ---------------------------------------------------------------------------
// QuestionFigure — renders one embedded figure beneath a question card
// (Phase 1 figure_embedder). Variant + needs_review styling mirrors the
// figure renderer in BlockRenderer.
// ---------------------------------------------------------------------------
function QuestionFigure({ figure }: { figure: EmbeddedFigure }) {
  const src = figure.image_url.startsWith("http")
    ? figure.image_url
    : `${API_BASE}${figure.image_url}`;
  const isRegen = figure.variant === "regen";
  const isAppended = figure.placement_kind !== "inline";
  const isReview = figure.placement_kind === "needs_review";
  const { selectedBookId } = useUI();
  const hide = useHideFigureReference();
  const onRemove = () => {
    if (!figure.ref_id || !selectedBookId) return;
    if (
      !window.confirm(
        `Remove ${figure.label || "this figure"} from this question? It won't appear here or in exports.`,
      )
    )
      return;
    hide.mutate({ refId: figure.ref_id, bookId: selectedBookId });
  };
  return (
    <div
      style={{
        marginTop: 6,
        marginBottom: 6,
        padding: 6,
        border: isAppended ? "1px dashed var(--border)" : "1px solid var(--border)",
        borderRadius: 6,
        background: "var(--bg2, #fafbfd)",
        maxWidth: 380,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: "0.66rem",
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
              fontSize: "0.58rem",
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
        {isAppended && (
          <span
            style={{
              fontSize: "0.58rem",
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
                ? "No body to attach to — please verify"
                : "No label match — auto-appended for review"
            }
          >
            ⚠ {isReview ? "Needs review" : "Auto-appended"}
          </span>
        )}
        <button
          type="button"
          onClick={onRemove}
          disabled={hide.isPending || !figure.ref_id}
          title="Remove this figure from this question (excluded from export)"
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: "1px solid var(--border)",
            borderRadius: 4,
            color: "var(--text3)",
            cursor: hide.isPending ? "default" : "pointer",
            fontSize: "0.66rem",
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
        }}
      />
      {figure.caption && (
        <div
          style={{
            fontSize: "0.66rem",
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
