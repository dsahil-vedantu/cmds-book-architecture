// Real Questions view — renders the extracted question rows for the
// selected section.

import { useState } from 'react';
import { Icon } from '../Icon';
import { MathMarkdown } from '../MathMarkdown';
import { API_BASE } from '../../api/client';
import { stripFigPlaceholders } from '../../lib/questionText';
import { restoreAllRejected } from '../../api/questions';
import type {
  ExtractedQuestion,
  SectionQuestions,
} from '../../api/questions';

type Props = {
  sectionRef: string | null;
  sectionQuestions: SectionQuestions | null;
  loading?: boolean;
  emptyMessage?: string;
  // Book-wide pending-review state. When pendingReviewCount > 0 we
  // surface a "Mark all reviewed" bulk action above the section
  // header. Optional so existing call sites that don't pass these
  // remain valid (banner just won't render).
  bankId?: string | null;
  pendingReviewCount?: number;
  onPendingResolved?: () => void;
};

export function QuestionsView({
  sectionRef,
  sectionQuestions,
  loading,
  emptyMessage,
  bankId,
  pendingReviewCount,
  onPendingResolved,
}: Props) {
  if (loading) {
    return (
      <div
        style={{
          flex: 1,
          padding: 48,
          color: 'var(--ink-500)',
          textAlign: 'center',
        }}
      >
        Loading questions…
      </div>
    );
  }

  if (!sectionRef) {
    return (
      <div
        style={{
          flex: 1,
          padding: 48,
          color: 'var(--ink-500)',
          textAlign: 'center',
        }}
      >
        Pick a section from the left to see its questions.
      </div>
    );
  }

  if (emptyMessage) {
    return (
      <div
        style={{
          flex: 1,
          padding: 48,
          color: 'var(--ink-500)',
          textAlign: 'center',
        }}
      >
        {emptyMessage}
      </div>
    );
  }

  if (!sectionQuestions || sectionQuestions.questions.length === 0) {
    return (
      <div
        style={{
          flex: 1,
          padding: 48,
          color: 'var(--ink-500)',
          textAlign: 'center',
        }}
      >
        No questions extracted from this section.
      </div>
    );
  }

  return (
    <div
      style={{
        flex: 1,
        overflowY: 'auto',
        padding: '28px 40px 56px',
        background: 'var(--bg)',
      }}
    >
      <div style={{ maxWidth: 760, margin: '0 auto' }}>
        {(pendingReviewCount ?? 0) > 0 && bankId && (
          <MarkAllReviewedBanner
            bankId={bankId}
            count={pendingReviewCount ?? 0}
            onDone={onPendingResolved}
          />
        )}
        <SectionHeader sectionQuestions={sectionQuestions} />
        <div style={{ marginTop: 18, display: 'flex', flexDirection: 'column', gap: 12 }}>
          {sectionQuestions.questions.map((q) => (
            <QuestionCard key={q.id} q={q} />
          ))}
        </div>
      </div>
    </div>
  );
}

function SectionHeader({
  sectionQuestions: sq,
}: {
  sectionQuestions: SectionQuestions;
}) {
  return (
    <div>
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: '0.12em',
          textTransform: 'uppercase',
          color: 'var(--ink-500)',
          fontFamily: 'var(--font-mono)',
        }}
      >
        {sq.section_ref}
      </div>
      <h1
        style={{
          fontSize: 24,
          fontWeight: 800,
          letterSpacing: '-0.02em',
          color: 'var(--ink-900)',
          margin: '6px 0 0',
          lineHeight: 1.2,
        }}
      >
        {sq.section_title || sq.section_ref}
      </h1>
      <div
        style={{
          fontSize: 12,
          color: 'var(--ink-500)',
          marginTop: 6,
          display: 'flex',
          alignItems: 'center',
          gap: 12,
        }}
      >
        <span className="badge ok">
          <span className="dot" />
          {sq.extracted} extracted
        </span>
        {sq.identified > sq.extracted && (
          <span>
            {sq.identified} identified · {sq.missed} missed
          </span>
        )}
      </div>
    </div>
  );
}

// Wing taxonomy regex — extracts the sub-type (e.g. "Essay Type Questions",
// "Level 1: Apply your Concepts") from a "PRACTICE QUESTIONS - X WING - Y"
// section_ref so it can render as a chip on the card.
const WING_SUBTYPE_RE = /^PRACTICE QUESTIONS - .+? WING - (.+)$/i;

function QuestionCard({ q }: { q: ExtractedQuestion }) {
  const isExample = q.kind === 'example';
  const accent = isExample ? 'var(--indigo-700)' : 'var(--red-600)';
  const label = isExample
    ? `Example ${q.question_number ?? ''}`.trim()
    : `Q${q.question_number ?? ''}`.trim();
  const subtypeMatch = q.section_ref?.match(WING_SUBTYPE_RE);
  const wingSubtype = subtypeMatch ? subtypeMatch[1] : null;

  return (
    <div
      style={{
        padding: '16px 18px',
        border: '1px solid var(--line)',
        borderLeft: `3px solid ${accent}`,
        borderRadius: 10,
        background: 'var(--surface)',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: 8,
        }}
      >
        <span
          className="qnum"
          style={{
            background: isExample ? 'var(--indigo-50)' : 'var(--red-50)',
            color: accent,
          }}
        >
          {label || (isExample ? 'EX' : 'Q')}
        </span>
        {q.question_type && (
          <span className="badge">{q.question_type}</span>
        )}
        {wingSubtype && (
          <span
            className="badge"
            title="Source category within the wing"
            style={{
              background: 'var(--indigo-50)',
              color: 'var(--indigo-700)',
              borderColor: 'var(--indigo-200)',
            }}
          >
            {wingSubtype}
          </span>
        )}
        {q.status === 'failed' && (
          <span className="badge regen">
            <span className="dot" />
            Failed
          </span>
        )}
        {q.is_hidden && (
          <span className="badge idle">
            <span className="dot" />
            Hidden
          </span>
        )}
        <span
          style={{
            marginLeft: 'auto',
            fontSize: 11,
            color: 'var(--ink-500)',
            fontFamily: 'var(--font-mono)',
          }}
        >
          {q.page_start ? `p.${q.page_start}` : ''}
        </span>
      </div>
      {/* Section anchor — always shown below the Q label so reviewers
          can confirm at a glance which section the question is filed
          under. Falls back to just the ref when no title available. */}
      {(q.section_title || q.section_ref) && (
        <div
          style={{
            fontSize: 11,
            color: 'var(--ink-500)',
            fontFamily: 'var(--font-mono)',
            letterSpacing: '0.04em',
            marginBottom: 8,
            paddingBottom: 6,
            borderBottom: '1px dashed var(--line)',
          }}
        >
          {q.section_ref}
          {q.section_title ? ` · ${q.section_title}` : ''}
        </div>
      )}
      <div
        style={{
          fontSize: 14.5,
          lineHeight: 1.65,
          color: 'var(--ink-900)',
        }}
      >
        {/* Q5: render question body through MathMarkdown → KaTeX + mhchem.
            $...$ math, \ce{} chemistry, $$...$$ display all render properly.
            stripFigPlaceholders removes {{fig:...}} markers first. */}
        <MathMarkdown>{stripFigPlaceholders(q.raw_text || '')}</MathMarkdown>
      </div>
      {/* Question vs Solution figure split — STRUCTURAL.
          Embedder writes body_target on every figure_reference:
            'question' → render under question stem
            'solution' → render inside solution <details> block
            null       → default to question (legacy / pre-body_target data)
          No inference, no string matching, no offset heuristics.
          The data carries its own routing. */}
      {(() => {
        const allFigs = q.embedded_figures ?? [];
        if (allFigs.length === 0) return null;
        const figsForQuestion: typeof allFigs = [];
        const figsForSolution: typeof allFigs = [];
        for (const ef of allFigs) {
          if (ef.body_target === 'solution') {
            figsForSolution.push(ef);
          } else {
            figsForQuestion.push(ef);
          }
        }
        // Stash on the parent scope-via-closure pattern. React JSX can't
        // declare vars, so we render below by reading from these arrays
        // (closure over render branch).
        (q as unknown as Record<string, unknown>).__figsQ = figsForQuestion;
        (q as unknown as Record<string, unknown>).__figsS = figsForSolution;
        return null;
      })()}

      {/* Question-body figures render here (under the question text). */}
      {(((q as unknown as Record<string, unknown>).__figsQ as typeof q.embedded_figures) ?? []).length > 0 && (
        <div
          style={{
            marginTop: 14,
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
          }}
        >
          {(((q as unknown as Record<string, unknown>).__figsQ as typeof q.embedded_figures) ?? []).map((ef) => (
            <FigureCard key={ef.ref_id} ef={ef} />
          ))}
        </div>
      )}
      {q.has_solution && q.solution_text && (
        <details
          style={{
            marginTop: 12,
            paddingTop: 10,
            borderTop: '1px dashed var(--line)',
          }}
        >
          <summary
            style={{
              cursor: 'pointer',
              fontSize: 12,
              fontWeight: 700,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              color: 'var(--ink-500)',
              userSelect: 'none',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <Icon name="check" size={12} /> Solution
          </summary>
          <div
            style={{
              marginTop: 8,
              padding: '12px 14px',
              background: 'var(--surface-2)',
              borderRadius: 8,
              fontSize: 13.5,
              lineHeight: 1.65,
              color: 'var(--ink-800)',
            }}
          >
            {/* Q5: solution text through MathMarkdown → KaTeX + mhchem */}
            <MathMarkdown>{stripFigPlaceholders(q.solution_text || '')}</MathMarkdown>
            {/* Solution-only figures rendered INSIDE the solution block
                (the embedder identified these via PATH 0 placeholder
                match in solution_text, or body_type=solution). */}
            {(((q as unknown as Record<string, unknown>).__figsS as typeof q.embedded_figures) ?? []).length > 0 && (
              <div
                style={{
                  marginTop: 12,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 12,
                }}
              >
                {(((q as unknown as Record<string, unknown>).__figsS as typeof q.embedded_figures) ?? []).map((ef) => (
                  <FigureCard key={ef.ref_id} ef={ef} />
                ))}
              </div>
            )}
          </div>
        </details>
      )}
    </div>
  );
}


/** Single embedded figure card (label / image / caption / description).
 *  Used by QuestionsView's question-figure and solution-figure renders. */
// Exported so the regen review page (RegenReviewPage → QuestionContent)
// renders question figures with the EXACT same card + placement as the
// extracted-content question view — figures land in the identical spot
// (under the stem for body_target=question, inside the solution block for
// body_target=solution). One component, no divergence.
export function FigureCard({
  ef,
}: {
  ef: {
    ref_id: string;
    label?: string;
    caption?: string;
    description?: string;
    image_url: string;
    variant?: 'original' | 'regen';
  };
}) {
  return (
    <div
      style={{
        border: '1px solid var(--line)',
        borderRadius: 10,
        overflow: 'hidden',
        background: 'var(--surface)',
      }}
    >
      <div
        style={{
          padding: '6px 12px',
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: '0.06em',
          color: 'var(--ink-700)',
          background: 'var(--surface-2)',
          borderBottom: '1px solid var(--line)',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
        }}
      >
        {ef.label || (ef.description ? 'Figure (unlabelled)' : 'Figure')}
        {ef.variant === 'regen' && (
          <span style={{ fontSize: 10, color: 'var(--indigo-700)', fontWeight: 700 }}>
            ✨ regen
          </span>
        )}
      </div>
      <img
        src={ef.image_url.startsWith('http') ? ef.image_url : `${API_BASE}${ef.image_url}`}
        alt={ef.caption || ef.label || ef.description || 'Figure'}
        style={{
          width: '100%',
          maxHeight: 360,
          objectFit: 'contain',
          background: 'var(--surface-2)',
          display: 'block',
        }}
      />
      {(ef.caption || ef.description) && (
        <div style={{ padding: '8px 12px', fontSize: 12.5, color: 'var(--ink-700)', lineHeight: 1.5 }}>
          {ef.caption && <MathMarkdown inline>{ef.caption}</MathMarkdown>}
          {ef.description && (
            <div
              style={{
                fontSize: ef.caption ? 11.5 : 12.5,
                color: ef.caption ? 'var(--ink-500)' : 'var(--ink-700)',
                marginTop: ef.caption ? 6 : 0,
                fontStyle: ef.caption ? 'italic' : 'normal',
              }}
            >
              {ef.description}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


function MarkAllReviewedBanner({
  bankId,
  count,
  onDone,
}: {
  bankId: string;
  count: number;
  onDone?: () => void;
}) {
  const [state, setState] = useState<
    | { kind: "idle" }
    | { kind: "loading" }
    | { kind: "done"; restored: number; rescued: number; attached: number }
    | { kind: "error"; message: string }
  >({ kind: "idle" });

  const onClick = async () => {
    if (state.kind === "loading") return;
    setState({ kind: "loading" });
    try {
      const r = await restoreAllRejected(bankId);
      setState({
        kind: "done",
        restored: r.restored ?? 0,
        rescued: r.solutions_rescued ?? 0,
        attached: r.figures_attached ?? 0,
      });
      if (onDone) onDone();
    } catch (e) {
      setState({
        kind: "error",
        message: e instanceof Error ? e.message : String(e),
      });
    }
  };

  if (state.kind === "done") {
    return (
      <div
        style={{
          padding: "10px 14px",
          background: "#F0F9F0",
          border: "1px solid #B6E2B6",
          borderRadius: 8,
          marginBottom: 14,
          fontSize: 13,
          color: "var(--ink-800)",
        }}
      >
        ✓ Marked {state.restored} item{state.restored === 1 ? "" : "s"} as reviewed
        {state.rescued > 0 && ` · rescued ${state.rescued} solution${state.rescued === 1 ? "" : "s"}`}
        {state.attached > 0 && ` · attached ${state.attached} figure${state.attached === 1 ? "" : "s"}`}
        . Refresh the page to see them under their sections.
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        padding: "10px 14px",
        background: "#FFF8E5",
        border: "1px solid #FFE38A",
        borderRadius: 8,
        marginBottom: 14,
        fontSize: 13,
        color: "var(--ink-800)",
      }}
    >
      <span>
        ⚠ <strong>{count}</strong> item{count === 1 ? "" : "s"} pending review across this book.
        {state.kind === "error" && (
          <span style={{ color: "var(--danger, #c33)", marginLeft: 8 }}>
            Error: {state.message}
          </span>
        )}
      </span>
      <button
        onClick={onClick}
        disabled={state.kind === "loading"}
        style={{
          padding: "6px 14px",
          fontSize: 13,
          fontWeight: 600,
          background: state.kind === "loading" ? "var(--ink-200)" : "var(--indigo-700)",
          color: "white",
          border: "none",
          borderRadius: 6,
          cursor: state.kind === "loading" ? "default" : "pointer",
          whiteSpace: "nowrap",
        }}
      >
        {state.kind === "loading" ? "Marking…" : `✓ Mark all reviewed (${count})`}
      </button>
    </div>
  );
}
