// Regen configuration page. Lands here from Review's "Start regeneration"
// CTA. User picks which of the 3 pipelines to run (master toggles), tunes
// per-pipeline params (matching the backend's existing param shapes),
// then clicks Run.
//
// Default behavior:
//   • All 3 toggles ON
//   • All sections included (no section_ids/section_refs sent — backend
//     treats null/omitted as "every section")
//   • Defaults for each param match the backend's default values
//
// Backend endpoints used (same as existing pipeline):
//   • POST /api/books/:id/regenerate                       (theory)
//   • POST /api/question-banks/:bank_id/regenerate         (questions)
//   • POST /api/books/:id/sections/:ref/regenerate-figures (figures, per section)
//
// Pipelines kicked here flow into the Regen progress page (Phase B.2).

import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { Icon } from '../components/Icon';
import { PipelineCard } from '../components/regen/PipelineCard';
import { TheoryParams } from '../components/regen/TheoryParams';
import { QuestionsParams } from '../components/regen/QuestionsParams';
import { FiguresParams } from '../components/regen/FiguresParams';
import { useBook } from '../api/books';
import { useSections } from '../api/sections';
import { useBookQuestions } from '../api/questions';
import { useBookFigures } from '../api/figures';
import {
  defaultFiguresParams,
  defaultQuestionsParams,
  defaultTheoryParams,
  postRegenFiguresAllSections,
  postRegenQuestions,
  postRegenTheory,
  type FiguresRegenParams,
  type QuestionsRegenParams,
  type TheoryRegenParams,
} from '../api/regen';
import { saveRegenKick } from '../api/regenPipeline';
import { useToast } from '../components/Toast';

export default function RegenConfigPage() {
  const { bookId } = useParams();
  const navigate = useNavigate();
  const { flash } = useToast();

  // Pull existing extracted-content state so we can show real counts on
  // each pipeline card ("Theory · 24 sections", etc.).
  const bookState = useBook(bookId);
  const sectionsState = useSections(bookId);
  const questionsState = useBookQuestions(bookId);
  const figuresState = useBookFigures(bookId);

  // Master toggles (default: all ON).
  const [theoryOn, setTheoryOn] = useState(true);
  const [questionsOn, setQuestionsOn] = useState(true);
  const [figuresOn, setFiguresOn] = useState(true);

  // Per-pipeline params.
  const [theoryParams, setTheoryParams] =
    useState<TheoryRegenParams>(defaultTheoryParams);
  const [questionsParams, setQuestionsParams] =
    useState<QuestionsRegenParams>(defaultQuestionsParams);
  const [figuresParams, setFiguresParams] =
    useState<FiguresRegenParams>(defaultFiguresParams);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Derived counts shown on cards.
  const counts = useMemo(() => {
    const sections =
      sectionsState.kind === 'ready' ? sectionsState.sections : [];
    const theoryCount = sections.filter(
      (s) => s.status === 'passed' || s.status === 'failed',
    ).length;
    const questionsCount =
      questionsState.kind === 'ready'
        ? questionsState.detail.total_questions
        : 0;
    const figuresCount =
      figuresState.kind === 'ready' ? figuresState.data.total_figures : 0;
    return { theoryCount, questionsCount, figuresCount };
  }, [sectionsState, questionsState, figuresState]);

  // Disable toggles when there's nothing to regen for that pipeline.
  useEffect(() => {
    if (counts.questionsCount === 0) setQuestionsOn(false);
    if (counts.figuresCount === 0) setFiguresOn(false);
  }, [counts.questionsCount, counts.figuresCount]);

  const canRun = theoryOn || questionsOn || figuresOn;

  // ─── Submit ─────────────────────────────────────────────────────
  const handleRun = async () => {
    if (!bookId) return;
    if (!canRun) return;
    setSubmitting(true);
    setSubmitError(null);

    const errors: string[] = [];
    const kick: Parameters<typeof saveRegenKick>[0] = {
      bookId,
      startedAt: Date.now(),
      theory: null,
      questions: null,
      figures: null,
    };

    // Theory
    if (theoryOn) {
      try {
        const r = await postRegenTheory(bookId, theoryParams, null);
        kick.theory = { job_id: r.job_id, regen_id: r.regen_id };
      } catch (e) {
        errors.push(
          `Theory: ${e instanceof Error ? e.message : 'Unknown error'}`,
        );
      }
    }

    // Questions — needs the latest bank_id
    if (questionsOn) {
      if (questionsState.kind !== 'ready') {
        errors.push('Questions: no question bank loaded yet');
      } else {
        try {
          const r = await postRegenQuestions(
            questionsState.bank.id,
            questionsParams,
          );
          kick.questions = { job_id: r.job_id, regen_id: r.regen_id };
        } catch (e) {
          errors.push(
            `Questions: ${e instanceof Error ? e.message : 'Unknown error'}`,
          );
        }
      }
    }

    // Figures — per-section, loop over sections that have figures
    if (figuresOn) {
      if (figuresState.kind !== 'ready') {
        errors.push('Figures: figures not loaded yet');
      } else {
        const refs = figuresState.data.sections
          .filter((s) => (s.figures?.length ?? 0) > 0)
          .map((s) => s.section_ref);
        try {
          const result = await postRegenFiguresAllSections(
            bookId,
            refs,
            figuresParams,
          );
          kick.figures = result.jobs;
          if (result.failures.length > 0) {
            errors.push(
              `Figures: ${result.failures.length}/${refs.length} sections failed to start`,
            );
          }
        } catch (e) {
          errors.push(
            `Figures: ${e instanceof Error ? e.message : 'Unknown error'}`,
          );
        }
      }
    }

    if (errors.length > 0 && !kick.theory && !kick.questions && !kick.figures) {
      // All three failed — surface and stay on config page.
      setSubmitError(errors.join(' · '));
      setSubmitting(false);
      return;
    }

    // Persist the kick so the progress page can attach + poll.
    saveRegenKick(kick);

    if (errors.length > 0) {
      flash(`Regeneration started with ${errors.length} warning(s)`);
    } else {
      flash('Regeneration started');
    }
    navigate(`/books/${bookId}/regenerate/progress`);
  };

  // ─── Loading + error states (after all hooks) ───────────────────
  if (bookState.kind === 'loading' || sectionsState.kind === 'loading') {
    return (
      <div className="content fade-up">
        <div className="content-narrow">
          <div className="card" style={{ padding: 28, color: 'var(--ink-500)' }}>
            Loading…
          </div>
        </div>
      </div>
    );
  }
  if (bookState.kind === 'error') {
    return (
      <div className="content fade-up">
        <div className="content-narrow">
          <div
            className="card"
            style={{
              padding: 24,
              background: 'var(--red-50)',
              border: '1px solid var(--red-100)',
            }}
          >
            <div style={{ fontWeight: 700, color: 'var(--red-700)' }}>
              Couldn't load book
            </div>
            <div style={{ fontSize: 12, marginTop: 4, fontFamily: 'var(--font-mono)' }}>
              {bookState.error}
            </div>
            <button
              className="btn btn-soft btn-sm"
              style={{ marginTop: 12 }}
              onClick={() => navigate(`/books/${bookId}/review`)}
            >
              Back to review
            </button>
          </div>
        </div>
      </div>
    );
  }

  const { book } = bookState.data;

  return (
    <div className="content fade-up">
      <div className="content-narrow" style={{ maxWidth: 920 }}>
        {/* Header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            marginBottom: 22,
          }}
        >
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => navigate(`/books/${bookId}/review`)}
          >
            <Icon name="arrow-l" size={14} /> Back to review
          </button>
          <div style={{ flex: 1 }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: '0.12em',
                textTransform: 'uppercase',
                color: 'var(--ink-500)',
                marginBottom: 2,
              }}
            >
              Configure regeneration
            </div>
            <h1
              className="page-title"
              style={{ fontSize: 26, lineHeight: 1.15, margin: 0 }}
            >
              {book.title}
            </h1>
          </div>
        </div>

        {/* Summary banner */}
        <div
          style={{
            padding: '14px 18px',
            background: 'var(--indigo-50)',
            border: '1px solid var(--indigo-100)',
            borderRadius: 12,
            marginBottom: 24,
            display: 'flex',
            alignItems: 'center',
            gap: 12,
          }}
        >
          <Icon name="wand" size={18} style={{ color: 'var(--indigo-700)' }} />
          <div style={{ flex: 1, fontSize: 13, color: 'var(--ink-700)' }}>
            <strong style={{ color: 'var(--ink-900)' }}>
              Default = every section regenerated.
            </strong>{' '}
            Toggle a pipeline OFF to skip it. Pipelines you turn on run
            against the same backend workers (theory regen / question regen /
            figure regen) — same prompts, same quality. Quality is identical
            to triggering each from the existing tool manually.
          </div>
        </div>

        {/* Pipeline cards */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <PipelineCard
            icon="layers"
            label="Theory"
            description="Rewrite extracted theory blocks with the chosen tone + intensity."
            count={counts.theoryCount}
            countLabel="sections"
            on={theoryOn}
            onToggle={() => setTheoryOn((v) => !v)}
          >
            <TheoryParams value={theoryParams} onChange={setTheoryParams} />
          </PipelineCard>

          <PipelineCard
            icon="question"
            label="Questions"
            description="Generate fresh question variants from each extracted question."
            count={counts.questionsCount}
            countLabel="questions"
            on={questionsOn && counts.questionsCount > 0}
            onToggle={() => {
              if (counts.questionsCount === 0) return;
              setQuestionsOn((v) => !v);
            }}
          >
            <QuestionsParams
              value={questionsParams}
              onChange={setQuestionsParams}
            />
          </PipelineCard>

          <PipelineCard
            icon="image"
            label="Figures"
            description="Redraw figures cleanly with AI. Loops over every section that has figures."
            count={counts.figuresCount}
            countLabel="figures"
            on={figuresOn && counts.figuresCount > 0}
            onToggle={() => {
              if (counts.figuresCount === 0) return;
              setFiguresOn((v) => !v);
            }}
          >
            <FiguresParams value={figuresParams} onChange={setFiguresParams} />
          </PipelineCard>
        </div>

        {/* Run CTA */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            gap: 10,
            marginTop: 24,
          }}
        >
          {submitError && (
            <div
              style={{
                flex: 1,
                padding: '10px 14px',
                background: 'var(--red-50)',
                border: '1px solid var(--red-100)',
                borderRadius: 10,
                color: 'var(--red-700)',
                fontSize: 13,
              }}
            >
              {submitError}
            </div>
          )}
          <button
            className="btn btn-ghost"
            onClick={() => navigate(`/books/${bookId}/review`)}
            disabled={submitting}
          >
            Cancel
          </button>
          <button
            className="btn btn-primary"
            onClick={() => void handleRun()}
            disabled={submitting || !canRun}
          >
            {submitting ? (
              <>
                <span className="spinner" /> Starting…
              </>
            ) : (
              <>
                <Icon name="play" size={14} /> Run regeneration
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
