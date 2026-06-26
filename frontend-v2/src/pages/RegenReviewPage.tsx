// Dedicated regenerated-content review page.
//
// Distinct from /books/:id/review (extracted content):
//   • Scroll-based full-chapter view (not click-to-show-one-section)
//   • Side rail TOC auto-jumps as the reader scrolls
//   • Per-section composer toolbar: reseed (regen with custom instruction),
//     preview (clean full-screen), QC warnings panel
//   • Top tabs: Theory | Questions | Figures
//   • Sub tabs: Regenerated (default) | Original | Comparison
//   • Top CTAs: Back to params · Export DOCX · Approve & Save
//
// Backend ALREADY supports:
//   POST /api/regenerations/:id/sections/:section_ref/retry → reseed
//   POST /api/regenerations/:id/save                        → approve
//   GET  /api/books/:id/export/docx                         → DOCX dl
//
// What's intentionally deferred (need new backend endpoints):
//   • Inline edit of blocks (PATCH section blocks)
//   • Section reorder / delete / add (PATCH regen blocks)
// These are post-demo work.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { API_BASE, ApiError, req } from '../api/client';
import { MathMarkdown } from '../components/MathMarkdown';
import { useBook } from '../api/books';
import { useSections, type Section } from '../api/sections';
import {
  useBookQuestions,
  type QuestionBankDetail,
  type RegenQuestionsResponse,
  type RegeneratedDiagram,
  listQuestionRegenerations,
  getRegenQuestions,
  retryRegenSection,
  saveQuestionRegeneration,
  hideQuestion,
  regenerateQuestionDiagram,
} from '../api/questions';
import {
  useBookFigures,
  type BookFigures,
  regenerateSectionFigures,
} from '../api/figures';
import { useLatestRegeneration } from '../api/regenerations';

import { Icon } from '../components/Icon';
import { sortByQuestionNumber } from '../lib/question-sort';
import { TheoryView } from '../components/review/TheoryView';
import { QuestionsView, FigureCard } from '../components/review/QuestionsView';
import { stripFigPlaceholders } from '../lib/questionText';
import { FiguresView } from '../components/review/FiguresView';

type TopTab = 'theory' | 'questions' | 'figures';
type SubTab = 'regenerated' | 'original' | 'compare';

type SchemaNode = {
  id?: string;
  title?: string;
  type?: string;
  content_types?: string[];
  subsections?: SchemaNode[];
};

export default function RegenReviewPage() {
  const { bookId } = useParams<{ bookId: string }>();
  const navigate = useNavigate();

  const bookState = useBook(bookId);
  // Two figure variants of the SAME sections so each sub-tab renders the
  // correct image without any client-side URL logic (the backend serializer
  // owns variant→URL composition):
  //   • sectionsState  (variant=original)    → Original tab + Comparison-left
  //   • regenFigState  (variant=regenerated) → Regenerated tab + Comparison-right
  // Section blocks are identical across both fetches; only embedded_figures'
  // image_url differs. Theory text comes from blocks_by_section (regen) or
  // section.blocks (original) — figures ride on whichever section object the
  // column uses.
  const sectionsState = useSections(bookId, 'original');
  const regenFigState = useSections(bookId, 'regenerated');
  const questionsState = useBookQuestions(bookId);
  const figuresState = useBookFigures(bookId);
  const regenState = useLatestRegeneration(bookId);

  // ── Question regeneration state ────────────────────────────────
  // Fetch latest question regeneration + its grouped questions so the
  // Questions tab can show regenerated content (and we can per-section
  // re-run with custom instructions).
  const [questionRegen, setQuestionRegen] =
    useState<RegenQuestionsResponse | null>(null);
  const [questionRegenLoading, setQuestionRegenLoading] = useState(false);
  const loadQuestionRegen = useCallback(async () => {
    if (!bookId) return;
    setQuestionRegenLoading(true);
    try {
      const regens = await listQuestionRegenerations(bookId);
      const latest = regens
        .filter((r) => r.status === 'ready' || r.status === 'saved' || r.status === 'partial')
        .sort((a, b) => (b.created_at > a.created_at ? 1 : -1))[0];
      if (!latest) {
        setQuestionRegen(null);
        return;
      }
      const detail = await getRegenQuestions(latest.id);
      setQuestionRegen(detail);
    } catch (_e) {
      setQuestionRegen(null);
    } finally {
      setQuestionRegenLoading(false);
    }
  }, [bookId]);
  useEffect(() => {
    void loadQuestionRegen();
  }, [loadQuestionRegen]);

  const [topTab, setTopTab] = useState<TopTab>('theory');

  // When the book has only QUESTION regen (no theory regen), land on the
  // Questions tab so the user sees their regenerated content instead of an
  // empty Theory tab. One-shot — never fights a later manual tab switch.
  const autoTabPicked = useRef(false);
  useEffect(() => {
    if (autoTabPicked.current) return;
    if (regenState.kind === 'loading' || questionRegenLoading) return;
    if (regenState.kind === 'empty' && questionRegen) setTopTab('questions');
    autoTabPicked.current = true;
  }, [regenState.kind, questionRegen, questionRegenLoading]);

  // Refetch fresh data when the user lands on a tab. The hooks only fetch
  // once on mount otherwise — if a background regen completes after mount
  // (and before the user opens the relevant tab), the cached state would
  // make the tab look stuck on empty / old data. This is cheap and snaps
  // the UI back to truth whenever the user switches view.
  useEffect(() => {
    if (topTab === 'theory') {
      void regenState.refetch?.();
    } else if (topTab === 'questions') {
      void questionsState.refetch?.();
    } else if (topTab === 'figures') {
      void figuresState.refetch?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topTab]);

  // Also poll while the user is sitting on a tab and a regen is mid-flight.
  // Without this, the user has to manually switch tabs to see fresh state
  // (the "tab stuck" complaint). Cheap GET, fires every 6s only when there's
  // a reason to (some regen reports a non-terminal status).
  useEffect(() => {
    const inFlightStatuses = new Set(['running', 'queued', 'pending', 'started']);
    const theoryBusy =
      regenState.kind === 'ready' &&
      typeof regenState.latest?.status === 'string' &&
      inFlightStatuses.has(regenState.latest.status);
    const questionsBusy =
      !!questionRegen?.regen?.status &&
      inFlightStatuses.has(questionRegen.regen.status);

    if (!theoryBusy && !questionsBusy) return;

    const id = window.setInterval(() => {
      if (theoryBusy) void regenState.refetch?.();
      if (questionsBusy) void loadQuestionRegen();
    }, 6000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [regenState.kind, (regenState as { latest?: { status?: string } }).latest?.status, questionRegen?.regen?.status]);
  const [subTab, setSubTab] = useState<SubTab>('regenerated');
  const [activeSectionId, setActiveSectionId] = useState<string | null>(null);
  const [reseedModal, setReseedModal] = useState<{
    open: boolean;
    sectionRef: string;
    sectionTitle: string;
  } | null>(null);
  const [previewModal, setPreviewModal] = useState<{
    open: boolean;
    section: Section | null;
  }>({ open: false, section: null });
  const [approving, setApproving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ── Schema walk for ordering + Cat A/B ─────────────────────────
  const { schemaOrder, catBIds, catAIds, excludedQs } = useMemo(() => {
    const order: Record<string, number> = {};
    const catB = new Set<string>();
    const catA = new Set<string>();
    const excluded: Array<{ section_id: string; title: string }> = [];
    if (bookState.kind !== 'ready')
      return { schemaOrder: order, catBIds: catB, catAIds: catA, excludedQs: excluded };
    let idx = 0;
    const walk = (nodes: SchemaNode[] | undefined) => {
      if (!nodes) return;
      for (const n of nodes) {
        if (n.type === 'excluded') continue;
        if (n.id && !(n.id in order)) {
          order[n.id] = idx++;
          const ct = (n.content_types ?? []).map((c) => String(c).toLowerCase().trim());
          // PURE Cat A = questions only. Mixed sections (theory + questions)
          // are PRIMARILY theory-bearing and belong in the Theory tab; their
          // Cat A nested items appear individually in the Questions tab.
          const isPureCatA = ct.includes('questions') && !ct.includes('theory');
          if (isPureCatA) catA.add(n.id);
          else catB.add(n.id);
        }
        walk(n.subsections);
      }
    };
    const schema = bookState.data.raw.schema_ as
      | { sections?: SchemaNode[]; excluded_sections?: Array<{ id?: string; title?: string }> }
      | null;
    walk(schema?.sections);
    // Collect end-of-chapter excluded sections (CLASSROOM WING / Unit Exercise / etc.).
    for (const ex of schema?.excluded_sections ?? []) {
      const title = (ex.title ?? '').trim();
      if (!title) continue;
      excluded.push({
        section_id: (ex.id ?? title).trim(),
        title,
      });
    }
    return { schemaOrder: order, catBIds: catB, catAIds: catA, excludedQs: excluded };
  }, [bookState]);

  const allSections = sectionsState.kind === 'ready' ? sectionsState.sections : [];
  // Regen-variant copy of each section, keyed by section_id. Same blocks,
  // but embedded_figures resolve to the regenerated image (with fallback to
  // original when no regen exists). The regen/comparison-right columns read
  // figures off this object so they show ↻ regenerated images while the
  // original/comparison-left columns keep the originals.
  const regenSectionById = useMemo(() => {
    const m = new Map<string, Section>();
    if (regenFigState.kind === 'ready') {
      for (const s of regenFigState.sections) m.set(s.section_id, s);
    }
    return m;
  }, [regenFigState]);
  const banksDetail = questionsState.kind === 'ready' ? questionsState.detail : null;
  const figuresData = figuresState.kind === 'ready' ? figuresState.data : null;
  // Full Figure[] for the whole book — the SAME prop the extract review
  // page (ReviewPage) feeds TheoryView. LABELLED theory figures render as
  // `{t:'fig', label}` blocks that TheoryView resolves to an image via this
  // `figures` list (figureByLabel → figureImageUrl). Without it, labelled
  // theory figures fall through to "Figure not available inline". Passing
  // it makes theory figures render in the regen review identically to the
  // extract page — no logic, same data source.
  const allFigures = useMemo(
    () => (figuresData ? figuresData.sections.flatMap((s) => s.figures) : []),
    [figuresData],
  );

  const sortBySchema = useCallback(
    (a: Section, b: Section) => {
      const ai = schemaOrder[a.section_id] ?? Number.MAX_SAFE_INTEGER;
      const bi = schemaOrder[b.section_id] ?? Number.MAX_SAFE_INTEGER;
      if (ai !== bi) return ai - bi;
      return a.section_id.localeCompare(b.section_id);
    },
    [schemaOrder],
  );

  const visibleSections: Section[] = useMemo(() => {
    if (topTab === 'theory') {
      return allSections
        .filter((s) => s.status === 'passed' || s.status === 'failed')
        .filter((s) => catBIds.has(s.section_id))
        .sort(sortBySchema);
    }
    if (topTab === 'questions') {
      const realCatA = allSections
        .filter((s) => catAIds.has(s.section_id))
        .sort(sortBySchema);

      // ONLY add end-of-chapter excluded BANKS (CLASSROOM WING, COMPETITION
      // WING, JEE SPECIAL WING, Unit Exercise, MCQ Bank, etc.) — NOT every
      // theory section that happens to have an entry in the bank.
      // Source: schema.excluded_sections (collected as excludedQs in the
      // earlier schema walk). Match on either:
      //   (a) the excluded title appearing as a bank section_ref, OR
      //   (b) the excluded section_id appearing as a bank section_ref.
      // Add a sidebar entry for EVERY regenerated/extracted section_ref that
      // isn't already a Cat A example. This is driven off the actual question
      // data (regen sections, else original bank sections) — NOT the schema's
      // bare excluded titles, which never matched: end-of-chapter banks are
      // stored as "<bank title>::<sub-wing>" (e.g. "CLASSROOM WING::Short
      // Answer Type Questions"), so a bare-title match dropped every excluded
      // bank from the sidebar. Driving it off the real refs guarantees all
      // excluded banks (and their sub-wings) always show.
      const knownIds = new Set(realCatA.map((s) => s.section_id));
      const dataSections =
        (questionRegen?.sections?.length
          ? questionRegen.sections
          : banksDetail?.sections) ?? [];
      const seenRefs = new Set<string>();
      const syntheticExcluded: Section[] = [];
      for (const ds of dataSections) {
        const ref = ds.section_ref;
        if (!ref || knownIds.has(ref) || seenRefs.has(ref)) continue;
        seenRefs.add(ref);
        syntheticExcluded.push({
          id: `syn-${ref}`,
          book_id: bookId,
          section_id: ref,
          title: ds.section_title || ref,
          blocks: [],
          attempts: 0,
          status: 'passed' as const,
          level: 2,
        } as unknown as Section);
      }
      return [...realCatA, ...syntheticExcluded];
    }
    // figures
    if (!figuresData) return [];
    const slugs = new Set(
      figuresData.sections
        .filter((s) => (s.figures?.length ?? 0) > 0)
        .map((s) => s.section_ref),
    );
    return allSections.filter((s) => slugs.has(s.section_id)).sort(sortBySchema);
  }, [topTab, allSections, catBIds, catAIds, sortBySchema, figuresData, questionRegen, banksDetail, bookId]);

  // ── Regen blocks by section (for "Regenerated" + "Compare") ──────
  const regenBlocksBySection: Record<string, Array<{ t: string; [k: string]: unknown }>> =
    regenState.kind === 'ready' ? regenState.latest.blocks_by_section || {} : {};

  // ── IntersectionObserver for auto-highlight + auto-scroll TOC ───
  const sectionRefs = useRef<Map<string, HTMLElement>>(new Map());
  const sidebarItemRefs = useRef<Map<string, HTMLElement>>(new Map());
  const mainScrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const inView = entries.filter((e) => e.isIntersecting);
        if (inView.length > 0) {
          // Pick the one closest to the top of the viewport
          const top = inView.sort(
            (a, b) => a.boundingClientRect.top - b.boundingClientRect.top,
          )[0];
          const id = top.target.id;
          setActiveSectionId(id);
          // auto-scroll the sidebar to keep active item visible
          const sidebarEl = sidebarItemRefs.current.get(id);
          if (sidebarEl) {
            sidebarEl.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
          }
        }
      },
      {
        root: mainScrollRef.current,
        rootMargin: '-30% 0px -60% 0px',
        threshold: 0,
      },
    );
    sectionRefs.current.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [visibleSections.length, topTab, subTab]);

  const scrollToSection = useCallback((sectionId: string) => {
    const el = sectionRefs.current.get(sectionId);
    if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' });
  }, []);

  // ── Approve & Save ─────────────────────────────────────────────
  // Backend endpoint POST /api/regenerations/:id/save requires
  // {"confirmed_section_ids": [...]}. We approve EVERY section in the
  // regen (user already reviewed them on this page).
  //
  // Where it saves:
  //   Backend trims regen.blocks_by_section to only confirmed sections
  //   and persists. The book in Library will now show this regen as
  //   its "approved" variant — Composer + Preview pull from it.
  //
  // After save: we redirect to Composer so the user immediately sees
  // their saved content + can edit/preview/export from there. This
  // matches the OLD prod flow.
  const [savedToast, setSavedToast] = useState(false);
  const handleApprove = useCallback(async () => {
    if (regenState.kind !== 'ready') return;
    setApproving(true);
    setError(null);
    try {
      const allSectionIds = Object.keys(
        regenState.latest.blocks_by_section ?? {},
      );
      await req(`/api/regenerations/${regenState.latest.id}/save`, {
        method: 'POST',
        body: JSON.stringify({ confirmed_section_ids: allSectionIds }),
      });
      // Re-seed the FinalDraft from the now-saved regen so Composer
      // has the latest content immediately. This is what OLD prod does.
      try {
        await req(`/api/books/${bookId}/final-draft/reseed`, { method: 'POST' });
      } catch {
        /* non-fatal — Composer can be re-seeded from its own button */
      }
      regenState.refetch();
      setSavedToast(true);
      // Auto-redirect to Composer after 1.2s — gives the user time to
      // see the success message, then takes them where they can act.
      setTimeout(() => {
        navigate(`/books/${bookId}/compose`);
      }, 1200);
    } catch (e) {
      setError(
        e instanceof ApiError ? `Backend ${e.status}: ${e.message}` :
        e instanceof Error ? e.message : 'Save failed',
      );
    } finally {
      setApproving(false);
    }
  }, [regenState, bookId, navigate]);

  // ── Reseed (per-section regen with custom instruction) ─────────
  // Backend endpoint: POST /api/regenerations/:id/sections/:section_id/rerun
  // Body: {"custom_instructions": "..."} (plural — singular was the bug).
  // Returns: {section_id, blocks} — synchronous, no polling needed.
  const submitReseed = useCallback(
    async (instruction: string) => {
      if (!reseedModal || !bookId) return;
      try {
        let regenId: string | null = null;
        if (regenState.kind === 'ready') {
          regenId = regenState.latest.id;
        } else {
          // No theory regen yet — start a fresh one for the whole book first,
          // then run the per-section reseed. Lets users hit "Regenerate this
          // section" on any section without having to start a book-wide
          // regen separately.
          setError('Starting fresh theory regen first…');
          const started = await req<{ regen_id: string }>(
            `/api/books/${bookId}/regenerate`,
            { method: 'POST', body: JSON.stringify({}) },
          );
          regenId = started.regen_id;
          // Wait a moment for backend to persist + index the regen row.
          await new Promise((r) => setTimeout(r, 1200));
        }
        await req(
          `/api/regenerations/${regenId}/sections/${encodeURIComponent(reseedModal.sectionRef)}/rerun`,
          {
            method: 'POST',
            body: JSON.stringify({ custom_instructions: instruction }),
          },
        );
        setReseedModal(null);
        regenState.refetch();
        setError('✓ Section regenerated with custom instruction.');
        setTimeout(() => setError(null), 2500);
      } catch (e) {
        setError(
          e instanceof ApiError ? `Backend ${e.status}: ${e.message}` :
          e instanceof Error ? e.message : 'Reseed failed',
        );
      }
    },
    [reseedModal, regenState, bookId],
  );

  // ── Export DOCX (regenerated content) ─────────────────────────
  // Uses the same endpoint pattern as OLD prod's FinalComposerPage:
  // /api/books/:id/export/docx?regen_id=... so the export includes the
  // regenerated theory. Falls back to plain extracted export if no
  // regen exists.
  const exportDocx = useCallback(() => {
    if (!bookId) return;
    const regenId = regenState.kind === 'ready' ? regenState.latest.id : null;
    const qs = regenId ? `?regen_id=${regenId}` : '';
    const url = `${API_BASE}/api/books/${bookId}/export/docx${qs}`;
    // Use a hidden <a> with download attr so the browser treats it as
    // a download (window.open opens it in a tab on some browsers).
    const a = document.createElement('a');
    a.href = url;
    a.download = '';
    document.body.appendChild(a);
    a.click();
    a.remove();
  }, [bookId, regenState]);

  // ── Re-seed (reseed the full final draft from the latest regen) ──
  // Calls POST /api/books/:id/final-draft/reseed which rebuilds the
  // FinalDraft items from the current regen. Used when the user wants
  // to discard manual composer edits and start over from regen output.
  const reseedFinalDraft = useCallback(async () => {
    if (!bookId) return;
    try {
      await req(`/api/books/${bookId}/final-draft/reseed`, { method: 'POST' });
      setError('✓ Re-seeded from regenerated content.');
      setTimeout(() => setError(null), 2500);
    } catch (e) {
      setError(
        e instanceof ApiError ? `Backend ${e.status}: ${e.message}` :
        e instanceof Error ? e.message : 'Re-seed failed',
      );
    }
  }, [bookId]);

  // ── Render ────────────────────────────────────────────────────
  if (bookState.kind === 'loading' || sectionsState.kind === 'loading') {
    return (
      <div className="content fade-up">
        <div className="content-narrow">
          <div className="card" style={{ padding: 28, color: 'var(--ink-500)' }}>
            Loading regenerated content…
          </div>
        </div>
      </div>
    );
  }
  if (bookState.kind === 'error') {
    return (
      <div className="content fade-up">
        <div className="content-narrow">
          <div className="card" style={{ padding: 28, color: 'var(--red-700)' }}>
            Couldn't load book: {bookState.error}
          </div>
        </div>
      </div>
    );
  }
  // Show the full empty state ONLY when there is NO regenerated content of
  // EITHER kind. A question-only regen (theory regen empty) must still render
  // the page so its Questions tab is visible — gating the whole page on the
  // theory regen was why a completed question regen showed "No content".
  if (regenState.kind === 'empty' && !questionRegen && !questionRegenLoading) {
    return (
      <div className="content fade-up">
        <div className="content-narrow" style={{ maxWidth: 720 }}>
          <div className="card" style={{ padding: 32, textAlign: 'center' }}>
            <Icon name="regen" size={32} />
            <h2 style={{ marginTop: 14, marginBottom: 6 }}>
              No regenerated content yet
            </h2>
            <div style={{ color: 'var(--ink-500)', fontSize: 14, marginBottom: 18 }}>
              Run regeneration first to see the regenerated content here.
            </div>
            <button
              className="btn btn-primary"
              onClick={() => navigate(`/books/${bookId}/regenerate`)}
            >
              <Icon name="regen" size={14} /> Configure regeneration
            </button>
          </div>
        </div>
      </div>
    );
  }
  if (regenState.kind === 'error') {
    return (
      <div className="content fade-up">
        <div className="content-narrow">
          <div className="card" style={{ padding: 28, color: 'var(--red-700)' }}>
            Couldn't load regeneration: {regenState.error}
          </div>
        </div>
      </div>
    );
  }

  const { book } = bookState.data;
  const hasRegen = regenState.kind === 'ready';

  return (
    <div
      className="fade-up"
      style={{
        flex: 1,
        minHeight: 0,
        padding: 0,
        display: 'flex',
        flexDirection: 'column',
        background: '#faf8f4',  // warm paper feel — distinct from review page
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '18px 28px 14px',
          borderBottom: '1px solid var(--line)',
          background: 'var(--surface)',
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          flexShrink: 0,
        }}
      >
        <div style={{ flex: 1 }}>
          <div
            style={{
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: '0.12em',
              textTransform: 'uppercase',
              color: 'var(--indigo-700)',
              marginBottom: 2,
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <Icon name="regen" size={11} /> Regenerated content
          </div>
          <h1
            style={{
              fontSize: 22,
              fontWeight: 800,
              letterSpacing: '-0.02em',
              color: 'var(--ink-900)',
              margin: 0,
            }}
          >
            {book.title}
          </h1>
        </div>
        {/* Back to chapter review */}
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => navigate(`/books/${bookId}/review`)}
          title="Back to chapter review"
        >
          <Icon name="arrow-l" size={14} /> Back
        </button>
        {/* Composer — opens the OLD prod composer (drag-reorder, edit, add) */}
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => navigate(`/books/${bookId}/compose`)}
          title="Open Composer — edit, reorder, add/remove sections"
        >
          <Icon name="layers" size={14} /> Composer
        </button>
        {/* Preview — clean read-only view of final document */}
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => navigate(`/books/${bookId}/preview`)}
          title="Open clean read-only preview of the regenerated chapter"
        >
          <Icon name="eye" size={14} /> Preview
        </button>
        {/* Re-seed — rebuilds final draft from latest regen */}
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => void reseedFinalDraft()}
          title="Re-seed: rebuild the final draft from the latest regenerated content (discards composer edits)"
        >
          <Icon name="regen" size={14} /> Re-seed
        </button>
        {/* Export DOCX */}
        <button
          className="btn btn-ghost btn-sm"
          onClick={exportDocx}
          title="Download regenerated chapter as DOCX"
        >
          <Icon name="docx" size={14} /> Export DOCX
        </button>
        {hasRegen && (
          <button
            className="btn btn-primary"
            onClick={() => void handleApprove()}
            disabled={approving}
            title="Approve all regenerated sections and save as the final draft"
          >
            {approving ? <span className="spinner" /> : <Icon name="check" size={14} />}
            Approve &amp; Save
          </button>
        )}
        {questionRegen?.regen?.id && (
          <button
            className="btn btn-soft btn-sm"
            onClick={async () => {
              try {
                await saveQuestionRegeneration(questionRegen.regen.id);
                setError('✓ Question regeneration saved.');
                setTimeout(() => setError(null), 2500);
                await loadQuestionRegen();
              } catch (e) {
                setError(
                  e instanceof Error
                    ? e.message
                    : 'Failed to save question regen',
                );
              }
            }}
            title="Approve & save the question regeneration as the final question bank"
          >
            <Icon name="check" size={13} /> Save Questions Regen
          </button>
        )}
      </div>

      {error && (
        <div
          style={{
            padding: '8px 28px',
            background: error.startsWith('✓') ? 'var(--success-bg, #DDF5E6)' : 'var(--red-50)',
            borderBottom: '1px solid ' + (error.startsWith('✓') ? '#A8DCC4' : 'var(--red-100)'),
            color: error.startsWith('✓') ? '#0B6A4F' : 'var(--red-700)',
            fontSize: 13,
          }}
        >
          {error}
        </div>
      )}
      {savedToast && (
        <div
          style={{
            position: 'fixed',
            top: 24,
            right: 24,
            zIndex: 300,
            padding: '14px 18px',
            background: '#0B6A4F',
            color: '#fff',
            borderRadius: 10,
            boxShadow: '0 10px 30px rgba(11, 106, 79, 0.35)',
            fontSize: 14,
            fontWeight: 600,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
          }}
        >
          <Icon name="check" size={14} />
          Saved to library · opening Composer…
        </div>
      )}

      {/* Top tabs */}
      <div
        style={{
          padding: '10px 28px 0',
          borderBottom: '1px solid var(--line)',
          background: 'var(--surface)',
          display: 'flex',
          gap: 2,
          flexShrink: 0,
        }}
      >
        {(['theory', 'questions', 'figures'] as TopTab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTopTab(t)}
            style={{
              padding: '10px 16px',
              background: 'transparent',
              border: 'none',
              borderBottom:
                topTab === t ? '2px solid var(--indigo-700)' : '2px solid transparent',
              color: topTab === t ? 'var(--ink-900)' : 'var(--ink-500)',
              fontWeight: topTab === t ? 700 : 500,
              fontSize: 13,
              cursor: 'pointer',
              textTransform: 'capitalize',
            }}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Sub tabs */}
      <div
        style={{
          padding: '10px 28px',
          background: 'var(--surface)',
          borderBottom: '1px solid var(--line)',
          display: 'flex',
          gap: 8,
          flexShrink: 0,
        }}
      >
        {/* Figures tab uses the FiguresView's own per-card ↔ Compare modal —
            the global Regenerated / Original / Comparison toggle doesn't
            apply there, so we hide it. Theory & Questions keep the strip. */}
        {topTab !== 'figures' &&
          (
            [
              { id: 'regenerated', label: '✨ Regenerated' },
              { id: 'original', label: 'Original' },
              { id: 'compare', label: 'Comparison' },
            ] as Array<{ id: SubTab; label: string }>
          ).map((sub) => (
            <button
              key={sub.id}
              onClick={() => setSubTab(sub.id)}
              className={`btn btn-sm ${subTab === sub.id ? 'btn-soft' : 'btn-ghost'}`}
            >
              {sub.label}
            </button>
          ))}
      </div>

      {/* Layout: sidebar + main scrollable */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {/* Side TOC */}
        <aside
          style={{
            width: 280,
            flexShrink: 0,
            background: 'var(--surface)',
            borderRight: '1px solid var(--line)',
            overflowY: 'auto',
            padding: '14px 0',
          }}
        >
          <div
            style={{
              padding: '0 18px 8px',
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: '0.1em',
              textTransform: 'uppercase',
              color: 'var(--ink-500)',
            }}
          >
            Sections · {visibleSections.length}
          </div>
          {visibleSections.map((s) => {
            const active = s.section_id === activeSectionId;
            return (
              <button
                key={s.id}
                ref={(el) => {
                  if (el) sidebarItemRefs.current.set(s.section_id, el);
                  else sidebarItemRefs.current.delete(s.section_id);
                }}
                onClick={() => scrollToSection(s.section_id)}
                style={{
                  display: 'block',
                  width: '100%',
                  padding: '8px 18px',
                  background: active ? 'var(--indigo-50)' : 'transparent',
                  borderLeft: active
                    ? '3px solid var(--indigo-700)'
                    : '3px solid transparent',
                  color: active ? 'var(--ink-900)' : 'var(--ink-700)',
                  fontWeight: active ? 600 : 500,
                  fontSize: 13,
                  textAlign: 'left',
                  cursor: 'pointer',
                  border: 'none',
                  borderBottom: 'none',
                }}
              >
                <div
                  style={{
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {s.title || s.section_id}
                </div>
                <div
                  style={{
                    fontSize: 10,
                    color: 'var(--ink-400)',
                    fontFamily: 'var(--font-mono)',
                    marginTop: 2,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {s.section_id}
                </div>
              </button>
            );
          })}
        </aside>

        {/* Main scrollable */}
        <div
          ref={mainScrollRef}
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '24px 32px',
            background: '#faf8f4',
          }}
        >
          {visibleSections.length === 0 && (
            <div style={{ padding: 48, color: 'var(--ink-500)', textAlign: 'center' }}>
              No sections to show in this tab.
            </div>
          )}
          {visibleSections.map((section) => (
            <SectionBlock
              key={section.id}
              section={section}
              regenSection={regenSectionById.get(section.section_id)}
              allFigures={allFigures}
              topTab={topTab}
              subTab={subTab}
              regenBlocks={regenBlocksBySection[section.section_id]}
              banksDetail={banksDetail}
              questionRegen={questionRegen}
              onQuestionHidden={async () => {
                // After a ✕ click on either Original or Regenerated side,
                // refetch BOTH lists so the hidden row disappears from view.
                await Promise.all([
                  loadQuestionRegen(),
                  questionsState.refetch?.(),
                ]);
              }}
              onSectionRetry={async (sectionRef, instruction) => {
                if (!questionRegen?.regen?.id) {
                  setError(
                    'No question regeneration to retry. Run a question regen first.',
                  );
                  return;
                }
                try {
                  await retryRegenSection(questionRegen.regen.id, {
                    section_ref: sectionRef,
                    custom_instructions: instruction || null,
                  });
                  setError('✓ Question section retry queued.');
                  setTimeout(() => setError(null), 2500);
                  // Refetch after a short delay so the worker has time
                  await new Promise((r) => setTimeout(r, 1500));
                  await loadQuestionRegen();
                } catch (e) {
                  setError(
                    e instanceof Error
                      ? e.message
                      : 'Question retry failed',
                  );
                }
              }}
              figuresData={figuresData}
              bookId={bookId}
              onFiguresRefetch={() => figuresState.refetch?.()}
              refSetter={(el) => {
                if (el) sectionRefs.current.set(section.section_id, el);
                else sectionRefs.current.delete(section.section_id);
              }}
              onReseed={() =>
                setReseedModal({
                  open: true,
                  sectionRef: section.section_id,
                  sectionTitle: section.title || section.section_id,
                })
              }
              onPreview={() => setPreviewModal({ open: true, section })}
            />
          ))}
        </div>
      </div>

      {/* Reseed modal */}
      {reseedModal?.open && (
        <ReseedModal
          sectionTitle={reseedModal.sectionTitle}
          // Return the promise so the modal can await it and keep its
          // spinner visible while Gemini regenerates (20-60s).
          onSubmit={(instruction) => submitReseed(instruction)}
          onClose={() => setReseedModal(null)}
        />
      )}

      {/* Preview modal */}
      {previewModal.open && previewModal.section && (
        <PreviewModal
          section={previewModal.section}
          regenBlocks={regenBlocksBySection[previewModal.section.section_id]}
          subTab={subTab}
          onClose={() => setPreviewModal({ open: false, section: null })}
        />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// SectionBlock — one section rendered in the scroll-based main area.
// ─────────────────────────────────────────────────────────────────

function SectionBlock({
  section,
  regenSection,
  allFigures,
  topTab,
  subTab,
  regenBlocks,
  banksDetail,
  questionRegen,
  onQuestionHidden,
  onSectionRetry,
  figuresData,
  bookId,
  onFiguresRefetch,
  refSetter,
  onReseed,
  onPreview,
}: {
  section: Section;
  regenSection: Section | undefined;
  // Full book Figure[] — passed straight to TheoryView so labelled theory
  // `fig` blocks resolve to images (same prop the extract page uses).
  allFigures: BookFigures['sections'][number]['figures'];
  topTab: TopTab;
  subTab: SubTab;
  regenBlocks: Array<{ t: string; [k: string]: unknown }> | undefined;
  banksDetail: QuestionBankDetail | null;
  questionRegen: RegenQuestionsResponse | null;
  onQuestionHidden: () => void | Promise<void>;
  onSectionRetry: (sectionRef: string, instruction: string) => void | Promise<void>;
  figuresData: BookFigures | null;
  bookId: string | undefined;
  onFiguresRefetch: () => void;
  refSetter: (el: HTMLDivElement | null) => void;
  onReseed: () => void;
  onPreview: () => void;
}) {
  const hasRegen = Array.isArray(regenBlocks) && regenBlocks.length > 0;

  // Compute simple QC delta for THIS section (block count + word count)
  const qc = useMemo(() => {
    if (subTab !== 'compare' && subTab !== 'regenerated') return null;
    const origBlocks = (section.blocks || []) as Array<{ t: string; c?: string }>;
    if (!hasRegen) return null;
    const origFree = origBlocks.filter(
      (b) => !['eq', 'def', 'fig', 'table', 'example', 'example_ref', 'exercise_ref', 'question_ref'].includes(b.t),
    );
    const regenFree = (regenBlocks ?? []).filter(
      (b) => !['eq', 'def', 'fig', 'table', 'example', 'example_ref', 'exercise_ref', 'question_ref'].includes(b.t),
    );
    const blockDelta = origFree.length - regenFree.length;
    const origWords = origBlocks.reduce(
      (n, b) => n + String(b.c ?? '').split(/\s+/).filter(Boolean).length,
      0,
    );
    const regenWords = (regenBlocks ?? []).reduce(
      (n, b) =>
        n + String((b as { c?: string }).c ?? '').split(/\s+/).filter(Boolean).length,
      0,
    );
    const ratio = origWords > 0 ? regenWords / origWords : 1;
    const warnings: string[] = [];
    if (blockDelta !== 0) warnings.push(`Block count drift: orig ${origFree.length} → regen ${regenFree.length}`);
    if (origWords > 50 && ratio < 0.7) warnings.push(`Shrunk to ${Math.round(ratio * 100)}% of original`);
    if (origWords > 50 && ratio > 1.3) warnings.push(`Expanded to ${Math.round(ratio * 100)}% of original`);
    return { ratio, blockDelta, origWords, regenWords, warnings };
  }, [section, regenBlocks, hasRegen, subTab]);

  return (
    <div
      id={section.section_id}
      ref={refSetter}
      style={{
        marginBottom: 44,
        scrollMarginTop: 20,
      }}
    >
      {/* Section header — single title (no slug duplication), inline composer icons */}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-end',
          gap: 10,
          marginBottom: 14,
          paddingBottom: 6,
          borderBottom: '1px solid var(--line-2)',
        }}
      >
        <h3
          style={{
            flex: 1,
            fontSize: 19,
            fontWeight: 800,
            color: 'var(--ink-900)',
            margin: 0,
            lineHeight: 1.2,
            letterSpacing: '-0.01em',
          }}
        >
          {section.title || section.section_id}
        </h3>
        {qc && <WordRatioBadge ratio={qc.ratio} />}
        {/* Per-section composer icons — visible only on theory/questions */}
        {(topTab === 'theory' || topTab === 'questions') && (
          <div style={{ display: 'flex', gap: 2 }}>
            <button
              className="btn btn-ghost btn-sm"
              onClick={onPreview}
              title="Open this section in a full-screen preview"
              style={{ padding: '4px 8px' }}
            >
              <Icon name="eye" size={13} />
            </button>
            {/* Regen button always visible. If no theory regen exists yet,
                the onReseed flow now starts a book-wide regen first (see
                submitReseed) and then queues this section. */}
            <button
              className="btn btn-ghost btn-sm"
              onClick={onReseed}
              title={hasRegen
                ? 'Reseed: regenerate this section with a custom instruction'
                : 'Regenerate this section (will start a fresh theory regen)'}
              style={{ padding: '4px 8px' }}
            >
              <Icon name="regen" size={13} />
            </button>
          </div>
        )}
      </div>

      {/* QC warnings — compact inline strip, only when present */}
      {qc && qc.warnings.length > 0 && (
        <div
          style={{
            padding: '6px 10px',
            background: '#FFF9E5',
            borderLeft: '3px solid #C28000',
            borderRadius: 4,
            color: '#8A5300',
            fontSize: 11,
            marginBottom: 12,
            display: 'flex',
            gap: 6,
            alignItems: 'baseline',
          }}
        >
          <span>⚠</span>
          <span>{qc.warnings.join(' · ')}</span>
        </div>
      )}

      {/* Body content based on subTab */}
      <div>
        {topTab === 'theory' && (
          <TheoryBody
            section={section}
            regenSection={regenSection}
            allFigures={allFigures}
            regenBlocks={regenBlocks}
            subTab={subTab}
          />
        )}
        {topTab === 'questions' && (
          <QuestionsBody
            section={section}
            banksDetail={banksDetail}
            subTab={subTab}
            questionRegen={questionRegen}
            onQuestionHidden={onQuestionHidden}
            onSectionRetry={onSectionRetry}
          />
        )}
        {topTab === 'figures' && (
          <FiguresBody
            section={section}
            figuresData={figuresData}
            bookId={bookId}
            onFiguresRefetch={onFiguresRefetch}
          />
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Body renderers — reuse existing view components.
// ─────────────────────────────────────────────────────────────────

function TheoryBody({
  section,
  regenSection,
  allFigures,
  regenBlocks,
  subTab,
}: {
  section: Section;
  // Same section_id, figures resolved to the regenerated image variant.
  // Used for the Regenerated tab + Comparison-right column so they show
  // ↻ regenerated figures while Original/Comparison-left keep originals.
  // Falls back to `section` (original-variant figures) until the regen
  // fetch loads.
  regenSection: Section | undefined;
  // Full book Figure[] → TheoryView's `figures` prop. Labelled theory
  // `fig` blocks resolve their image through this (figureByLabel). Same
  // data the extract review page passes, so theory figures render here
  // exactly as they do post-extraction.
  allFigures: BookFigures['sections'][number]['figures'];
  regenBlocks: Array<{ t: string; [k: string]: unknown }> | undefined;
  subTab: SubTab;
}) {
  const origBlocks = (section.blocks || []) as Array<{ t: string; [k: string]: unknown }>;
  const hasRegen = Array.isArray(regenBlocks) && regenBlocks.length > 0;
  // Section object whose embedded_figures carry the regenerated image URLs.
  const regenFigSection = regenSection ?? section;
  // Local alias for TheoryView's Block union — its actual definition is in
  // the TheoryView module; we treat blocks as opaque here.
  type Block = { t: string; [k: string]: unknown };

  // Original tab (or no regen yet): original blocks + original-variant
  // figures. `section` is the variant=original fetch, so figures here are
  // always the originals even when a regen image exists.
  if (subTab === 'original' || !hasRegen) {
    return <TheoryView section={section} figures={allFigures} hideHeader flat />;
  }

  // Regenerated tab: regen blocks + regenerated-variant figures.
  if (subTab === 'regenerated') {
    return (
      <TheoryView
        section={regenFigSection}
        figures={allFigures}
        blocksOverride={regenBlocks as Block[]}
        hideHeader
        flat
      />
    );
  }

  // compare — side-by-side with subtle column tint + divider + compact text
  return (
    <div
      className="regen-compare"
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1px 1fr',
        gap: 14,
        fontSize: 13,
      }}
    >
      <div
        style={{
          background: 'var(--surface)',
          border: '1px solid var(--line)',
          borderRadius: 10,
          padding: '14px 16px',
        }}
      >
        <div
          style={{
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            color: 'var(--ink-500)',
            marginBottom: 10,
            paddingBottom: 8,
            borderBottom: '1px solid var(--line)',
          }}
        >
          Original
        </div>
        <TheoryView
          section={section}
          figures={allFigures}
          blocksOverride={origBlocks as Block[]}
          hideHeader
          flat
        />
      </div>
      {/* vertical divider */}
      <div
        style={{
          background: 'var(--line)',
          margin: '0',
        }}
      />
      <div
        style={{
          background: 'var(--indigo-50)',
          border: '1px solid var(--indigo-100)',
          borderRadius: 10,
          padding: '14px 16px',
        }}
      >
        <div
          style={{
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
            color: 'var(--indigo-700)',
            marginBottom: 10,
            paddingBottom: 8,
            borderBottom: '1px solid var(--indigo-100)',
          }}
        >
          ✨ Regenerated
        </div>
        <TheoryView
          section={regenFigSection}
          figures={allFigures}
          blocksOverride={regenBlocks as Block[]}
          hideHeader
          flat
        />
      </div>
    </div>
  );
}

function QuestionsBody({
  section,
  banksDetail,
  subTab,
  questionRegen,
  onQuestionHidden,
  onSectionRetry,
}: {
  section: Section;
  banksDetail: QuestionBankDetail | null;
  subTab: SubTab;
  questionRegen: RegenQuestionsResponse | null;
  onQuestionHidden: () => void | Promise<void>;
  onSectionRetry: (sectionRef: string, instruction: string) => void | Promise<void>;
}) {
  const originalQs =
    banksDetail?.sections.find((s) => s.section_ref === section.section_id) ?? null;
  const regenQs =
    questionRegen?.sections.find((s) => s.section_ref === section.section_id) ?? null;

  // Pick which data to display based on subTab.
  // Regenerated (default): show regen data if present, else fall back to original.
  // Original: show original bank.
  // Compare: side-by-side.
  const sectionQuestions =
    subTab === 'original'
      ? originalQs
      : regenQs ?? originalQs;

  // Reseed button + dialog state for this section.
  const [retryModalOpen, setRetryModalOpen] = useState(false);
  const [retryInstruction, setRetryInstruction] = useState('');
  const [retryBusy, setRetryBusy] = useState(false);

  // Per-section retry button (always available when there's a regen)
  const retryButton = questionRegen?.regen?.id ? (
    <div style={{ padding: '12px 24px 0 24px' }}>
      <button
        className="btn btn-ghost btn-sm"
        onClick={() => setRetryModalOpen(true)}
        title="Regenerate this section's questions with a custom instruction"
        style={{ padding: '6px 12px', fontSize: 12 }}
      >
        <Icon name="regen" size={12} /> Reseed this section
      </button>
    </div>
  ) : null;

  // Reseed dialog (inline)
  const retryDialog = retryModalOpen ? (
    <div
      role="dialog"
      aria-modal="true"
      style={{
        position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)',
        zIndex: 60, display: 'grid', placeItems: 'center', padding: 24,
      }}
      onClick={() => setRetryModalOpen(false)}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--surface)', borderRadius: 12, padding: 24,
          maxWidth: 560, width: '100%', boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
        }}
      >
        <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>
          <Icon name="regen" size={16} /> Reseed this section (questions)
        </div>
        <div style={{ fontSize: 13, color: 'var(--ink-600)', marginBottom: 4 }}>
          Section: <strong>{section.title || section.section_id}</strong>
        </div>
        <div style={{ fontSize: 13, color: 'var(--ink-500)', marginBottom: 14 }}>
          Add a custom instruction for this section's question regeneration.
          The current global params still apply; this is layered on top.
        </div>
        <textarea
          value={retryInstruction}
          onChange={(e) => setRetryInstruction(e.target.value)}
          rows={5}
          style={{
            width: '100%', padding: 10, border: '1px solid var(--line)',
            borderRadius: 8, fontSize: 13, fontFamily: 'inherit', resize: 'vertical',
          }}
          placeholder="e.g. add more numerical problems, or rephrase in simpler English"
          autoFocus
        />
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 14 }}>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setRetryModalOpen(false)}
            disabled={retryBusy}
          >
            Cancel
          </button>
          <button
            className="btn btn-primary btn-sm"
            disabled={retryBusy}
            onClick={async () => {
              setRetryBusy(true);
              try {
                await onSectionRetry(section.section_id, retryInstruction);
                setRetryInstruction('');
                setRetryModalOpen(false);
              } finally {
                setRetryBusy(false);
              }
            }}
          >
            {retryBusy ? (
              <>
                <span className="spinner" /> Regenerating… (this can take 30-60s)
              </>
            ) : (
              <>
                <Icon name="regen" size={12} /> Regenerate this section
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  ) : null;

  // Empty-section guard: when neither original nor regen has any data
  // for this section, surface a clear message instead of rendering blank
  // panels (which made the tab look broken during partial loads).
  if (!originalQs && !regenQs) {
    return (
      <>
        {retryButton}
        <div style={{ padding: 40, color: 'var(--ink-500)', textAlign: 'center' }}>
          No questions extracted for this section yet.
        </div>
        {retryDialog}
      </>
    );
  }

  // Compare mode — per-QUESTION pairing: each original question shown
  // alongside its regen variants. Sections that have multiple questions
  // (Exercise / CLASSROOM WING etc.) render one pair per question for
  // clear visibility. Much better than the section-level "all-orig on
  // left, all-regen on right" stacking.
  if (subTab === 'compare') {
    // Sort by textbook-original question_number so the Compare tab's
    // side-by-side pairs render in proper sequential order (1, 2, 3, ...,
    // 5(a), 5(b), 5(i), ...) — matches the DOCX export + Preview ordering.
    // Render-layer sort only; the underlying API queries are untouched.
    const origList = sortByQuestionNumber(originalQs?.questions ?? []);
    const regenList = sortByQuestionNumber(regenQs?.questions ?? []);
    // Build {original_id → [regen variants]} map.
    const variantsByOriginal = new Map<string, typeof regenList>();
    for (const rq of regenList) {
      const srcId = (rq as { source_question_id?: string }).source_question_id;
      if (srcId) {
        const arr = variantsByOriginal.get(srcId) ?? [];
        arr.push(rq);
        variantsByOriginal.set(srcId, arr);
      }
    }
    return (
      <>
        {retryButton}
        <div
          style={{
            padding: '16px 20px 56px',
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
            background: 'var(--bg)',
          }}
        >
          {origList.length === 0 && (
            <div
              style={{
                padding: 32,
                textAlign: 'center',
                color: 'var(--ink-500)',
                fontSize: 13,
              }}
            >
              No original questions for this section.
            </div>
          )}
          {origList.map((oq, idx) => {
            const variants = variantsByOriginal.get(oq.id) ?? [];
            return (
              <div
                key={oq.id}
                className="card"
                style={{
                  padding: 0,
                  overflow: 'hidden',
                }}
              >
                {/* Compact header for the question pair */}
                <div
                  style={{
                    padding: '8px 14px',
                    background: 'var(--surface-2)',
                    borderBottom: '1px solid var(--line)',
                    fontSize: 11,
                    fontWeight: 700,
                    color: 'var(--ink-700)',
                    letterSpacing: '0.04em',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                  }}
                >
                  <span>Q{idx + 1}</span>
                  {oq.question_number && (
                    <span style={{ color: 'var(--ink-500)' }}>· #{oq.question_number}</span>
                  )}
                  {oq.page_start && (
                    <span style={{ color: 'var(--ink-500)' }}>· p.{oq.page_start}</span>
                  )}
                  <span style={{ marginLeft: 'auto', color: 'var(--indigo-700)' }}>
                    {variants.length} variant{variants.length === 1 ? '' : 's'}
                  </span>
                </div>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '1fr 1fr',
                    gap: 0,
                  }}
                >
                  {/* Original side */}
                  <div style={{ padding: '12px 16px', borderRight: '1px solid var(--line)' }}>
                    <div
                      style={{
                        fontSize: 10,
                        fontWeight: 700,
                        letterSpacing: '0.1em',
                        textTransform: 'uppercase',
                        color: 'var(--ink-500)',
                        marginBottom: 6,
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                      }}
                    >
                      <span>Original</span>
                      <button
                        title="Hide this question"
                        onClick={async () => {
                          try {
                            await hideQuestion(oq.id);
                            // Refetch so the hidden row disappears from view.
                            await onQuestionHidden();
                          } catch (_e) {
                            // ignored — server will retry on next refetch
                          }
                        }}
                        style={{
                          marginLeft: 'auto',
                          border: 'none',
                          background: 'transparent',
                          color: 'var(--ink-400)',
                          fontSize: 14,
                          cursor: 'pointer',
                          padding: 2,
                        }}
                      >
                        ✕
                      </button>
                    </div>
                    <QuestionContent question={oq} />
                  </div>
                  {/* Regenerated variants */}
                  <div style={{ padding: '12px 16px', background: 'var(--indigo-50)' }}>
                    <div
                      style={{
                        fontSize: 10,
                        fontWeight: 700,
                        letterSpacing: '0.1em',
                        textTransform: 'uppercase',
                        color: 'var(--indigo-700)',
                        marginBottom: 6,
                      }}
                    >
                      ✨ Regenerated
                    </div>
                    {variants.length === 0 ? (
                      <div style={{ fontSize: 12, color: 'var(--ink-500)', fontStyle: 'italic' }}>
                        No regenerated variant yet.
                      </div>
                    ) : (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                        {variants.map((rq, vidx) => (
                          <div
                            key={rq.id}
                            style={{
                              paddingTop: vidx === 0 ? 0 : 8,
                              borderTop: vidx === 0 ? 'none' : '1px dashed var(--line)',
                            }}
                          >
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
                              {variants.length > 1 && (
                                <span
                                  style={{
                                    fontSize: 10,
                                    fontWeight: 700,
                                    color: 'var(--indigo-700)',
                                  }}
                                >
                                  Variant {vidx + 1}
                                </span>
                              )}
                              <button
                                title="Hide this variant"
                                onClick={async () => {
                                  try {
                                    await hideQuestion(rq.id);
                                    await onQuestionHidden();
                                  } catch (_e) {/* ignored */}
                                }}
                                style={{
                                  marginLeft: 'auto',
                                  border: 'none',
                                  background: 'transparent',
                                  color: 'var(--ink-400)',
                                  fontSize: 14,
                                  cursor: 'pointer',
                                  padding: 2,
                                }}
                              >
                                ✕
                              </button>
                            </div>
                            <QuestionContent question={rq} />
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
        {retryDialog}
      </>
    );
  }

  // Regenerated (default) and Original tabs — single view.
  return (
    <>
      {retryButton}
      <QuestionsView
        sectionRef={section.section_id}
        sectionQuestions={sectionQuestions}
      />
      {retryDialog}
    </>
  );
}

// Per-question content renderer — question text + collapsible SOLUTION
// (matches the look of QuestionsView.QuestionCard so the compare view is
// consistent with the regular single-tab view).
type RegenQEmbeddedFigure = {
  ref_id: string;
  label?: string;
  caption?: string;
  description?: string;
  image_url: string;
  variant?: 'original' | 'regen';
  body_target?: 'question' | 'solution' | null;
};

function QuestionContent({
  question,
}: {
  question: {
    id?: string;
    raw_text?: string;
    has_solution?: boolean;
    solution_text?: string | null;
    embedded_figures?: RegenQEmbeddedFigure[];
    qc_local?: {
      regen_failed?: { retained_original?: boolean; reason?: string };
    } | null;
    regenerated_diagram?: RegeneratedDiagram | null;
  };
}) {
  // No-skip fallback badge: when regeneration produced 0 variants for a
  // source, the backend retains the ORIGINAL question flagged here so it is
  // never silently dropped. Surface that clearly so the user knows this
  // "variant" is the original verbatim and can retry the section.
  const regenFailed = question.qc_local?.regen_failed;
  const retainedOriginal = Boolean(regenFailed?.retained_original);
  // Same STRUCTURAL split as the extracted-content question view
  // (QuestionsView): body_target routes each figure under the question
  // stem vs inside the solution block. No inference — the data carries its
  // own routing. NULL body_target (legacy) defaults to the question side.
  const allFigs = question.embedded_figures ?? [];
  const figsForQuestion: RegenQEmbeddedFigure[] = [];
  const figsForSolution: RegenQEmbeddedFigure[] = [];
  for (const ef of allFigs) {
    if (ef.body_target === 'solution') figsForSolution.push(ef);
    else figsForQuestion.push(ef);
  }

  // Step 2 — regenerated LaTeX/SVG diagram + reseed. Local override so a
  // reseed shows instantly without a full refetch; falls back to the prop.
  const [override, setOverride] = useState<RegeneratedDiagram | null>(null);
  const [reseedOpen, setReseedOpen] = useState(false);
  const [reseedInstr, setReseedInstr] = useState('');
  const [reseedBusy, setReseedBusy] = useState(false);
  const [reseedErr, setReseedErr] = useState<string | null>(null);
  const diagram = override ?? question.regenerated_diagram;
  // Option X — show ONLY the new diagram when it exists (never the stale
  // original). image_regen_hint absence + no diagram → nothing to show.
  const showDiagram = !!(
    diagram && !diagram.fallback_to_original && diagram.svg_preview
  );

  const runReseed = async () => {
    if (!question.id) return;
    setReseedBusy(true);
    setReseedErr(null);
    try {
      const res = await regenerateQuestionDiagram(
        question.id,
        reseedInstr.trim() || null,
      );
      setOverride(res.regenerated_diagram);
      setReseedOpen(false);
      setReseedInstr('');
    } catch (e) {
      setReseedErr(e instanceof Error ? e.message : 'Reseed failed');
    } finally {
      setReseedBusy(false);
    }
  };
  return (
    <>
      {retainedOriginal && (
        <div
          title={regenFailed?.reason || 'Regeneration produced no variants'}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            marginBottom: 6,
            padding: '3px 8px',
            borderRadius: 6,
            background: '#FFF4E5',
            border: '1px solid #E0A458',
            color: '#8A5300',
            fontSize: 11,
            fontWeight: 700,
          }}
        >
          ⚠ Couldn't regenerate — original retained
        </div>
      )}
      <div
        style={{
          fontSize: 13.5,
          lineHeight: 1.55,
          color: 'var(--ink-900)',
        }}
      >
        {question.raw_text ? (
          <MathMarkdown>{stripFigPlaceholders(question.raw_text)}</MathMarkdown>
        ) : (
          <em style={{ color: 'var(--ink-400)' }}>(no text)</em>
        )}
      </div>

      {/* Question-body figures — shown only for SOURCE/original questions
          (regen variants carry no inherited figure; they show the diagram
          below). Identical card/placement to the extract review page. */}
      {figsForQuestion.length > 0 && (
        <div
          style={{
            marginTop: 10,
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
          }}
        >
          {figsForQuestion.map((ef) => (
            <FigureCard key={ef.ref_id} ef={ef} />
          ))}
        </div>
      )}

      {/* Step 2 — regenerated vector diagram (regen variants only). Shows in
          place of the original figure; Option X = new diagram or nothing. */}
      {diagram && !diagram.fallback_to_original && diagram.svg_preview && (
        <div
          style={{
            marginTop: 12,
            padding: 12,
            borderRadius: 8,
            border: '1px solid var(--teal-200, #99f6e4)',
            background: 'var(--teal-50, #f0fdfa)',
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 8,
              marginBottom: 8,
              paddingBottom: 6,
              borderBottom: '1px solid var(--teal-100, #ccfbf1)',
            }}
          >
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: '0.08em',
                textTransform: 'uppercase',
                color: 'var(--teal-700, #0f766e)',
              }}
            >
              ✨ Vector Diagram{diagram.subject ? ` · ${diagram.subject}` : ''}
            </span>
            <span style={{ fontSize: 10, fontStyle: 'italic', color: 'var(--ink-400)' }}>
              Generated from context
            </span>
          </div>
          <div
            style={{
              display: 'flex',
              justifyContent: 'center',
              alignItems: 'center',
              background: '#fff',
              padding: 12,
              borderRadius: 6,
              border: '1px solid var(--line)',
              overflow: 'auto',
            }}
            dangerouslySetInnerHTML={{ __html: diagram.svg_preview }}
          />
          {diagram.latex_code && (
            <details style={{ marginTop: 10 }}>
              <summary
                style={{
                  cursor: 'pointer',
                  fontSize: 11,
                  fontWeight: 600,
                  color: 'var(--teal-700, #0f766e)',
                  userSelect: 'none',
                }}
              >
                Show LaTeX code
              </summary>
              <div style={{ position: 'relative', marginTop: 6 }}>
                <button
                  onClick={() => {
                    void navigator.clipboard?.writeText(diagram.latex_code);
                  }}
                  style={{
                    position: 'absolute',
                    right: 6,
                    top: 6,
                    fontSize: 10,
                    padding: '2px 8px',
                    borderRadius: 4,
                    border: '1px solid #374151',
                    background: '#1f2937',
                    color: '#e5e7eb',
                    cursor: 'pointer',
                  }}
                >
                  Copy
                </button>
                <pre
                  style={{
                    margin: 0,
                    padding: 12,
                    maxHeight: 200,
                    overflow: 'auto',
                    borderRadius: 6,
                    background: '#111827',
                    color: '#e5e7eb',
                    fontSize: 11,
                    lineHeight: 1.5,
                    fontFamily: 'var(--font-mono)',
                    whiteSpace: 'pre-wrap',
                  }}
                >
                  {diagram.latex_code}
                </pre>
              </div>
            </details>
          )}
          {diagram.description && (
            <div style={{ marginTop: 8, fontSize: 11, color: 'var(--ink-500)' }}>
              {diagram.description}
            </div>
          )}
        </div>
      )}
      {diagram?.fallback_to_original && (
        <div
          style={{
            marginTop: 12,
            padding: '8px 12px',
            borderRadius: 6,
            border: '1px solid var(--amber-200, #fde68a)',
            background: 'var(--amber-50, #fffbeb)',
            fontSize: 11.5,
            color: 'var(--amber-800, #92400e)',
          }}
        >
          💡 <strong>Diagram unavailable:</strong> a vector diagram couldn’t be
          generated for this regenerated question (e.g. a complex
          biological/organic graphic). No figure is shown for the regenerated
          question — use “Reseed diagram” below to try again.
        </div>
      )}
      {question.id && diagram && (
        <div style={{ marginTop: 8 }}>
          {!reseedOpen ? (
            <button
              onClick={() => setReseedOpen(true)}
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: 'var(--teal-700, #0f766e)',
                background: 'transparent',
                border: '1px dashed var(--teal-200, #99f6e4)',
                borderRadius: 6,
                padding: '4px 10px',
                cursor: 'pointer',
              }}
            >
              🔁 Reseed diagram
            </button>
          ) : (
            <div
              style={{
                padding: 10,
                borderRadius: 6,
                border: '1px solid var(--teal-200, #99f6e4)',
                background: 'var(--teal-50, #f0fdfa)',
              }}
            >
              <textarea
                value={reseedInstr}
                onChange={(e) => setReseedInstr(e.target.value)}
                placeholder="Optional: how should the diagram change? e.g. 'label the angle as 45°', 'add the normal as a dashed line', 'use a clearer scale'"
                rows={2}
                disabled={reseedBusy}
                style={{
                  width: '100%',
                  boxSizing: 'border-box',
                  fontSize: 12,
                  padding: 8,
                  borderRadius: 4,
                  border: '1px solid var(--line)',
                  resize: 'vertical',
                  fontFamily: 'inherit',
                }}
              />
              {reseedErr && (
                <div style={{ marginTop: 6, fontSize: 11, color: 'var(--red-600, #dc2626)' }}>
                  {reseedErr}
                </div>
              )}
              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <button
                  onClick={runReseed}
                  disabled={reseedBusy}
                  style={{
                    fontSize: 11,
                    fontWeight: 600,
                    color: '#fff',
                    background: reseedBusy
                      ? 'var(--ink-300, #cbd5e1)'
                      : 'var(--teal-700, #0f766e)',
                    border: 'none',
                    borderRadius: 6,
                    padding: '5px 12px',
                    cursor: reseedBusy ? 'default' : 'pointer',
                  }}
                >
                  {reseedBusy ? 'Regenerating…' : 'Regenerate diagram'}
                </button>
                <button
                  onClick={() => {
                    setReseedOpen(false);
                    setReseedErr(null);
                  }}
                  disabled={reseedBusy}
                  style={{
                    fontSize: 11,
                    color: 'var(--ink-500)',
                    background: 'transparent',
                    border: '1px solid var(--line)',
                    borderRadius: 6,
                    padding: '5px 12px',
                    cursor: reseedBusy ? 'default' : 'pointer',
                  }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
      {question.has_solution && question.solution_text && (
        <details
          style={{
            marginTop: 10,
            paddingTop: 8,
            borderTop: '1px dashed var(--line)',
          }}
        >
          <summary
            style={{
              cursor: 'pointer',
              fontSize: 11,
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
            <Icon name="check" size={11} /> Solution
          </summary>
          <div
            style={{
              marginTop: 6,
              padding: '10px 12px',
              background: 'var(--surface-2)',
              borderRadius: 6,
              fontSize: 12.5,
              lineHeight: 1.6,
              color: 'var(--ink-800)',
            }}
          >
            <MathMarkdown>{stripFigPlaceholders(question.solution_text || '')}</MathMarkdown>
            {/* Solution-body figures — inside the solution block, same as
                the extract review page. */}
            {figsForSolution.length > 0 && (
              <div
                style={{
                  marginTop: 10,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 10,
                }}
              >
                {figsForSolution.map((ef) => (
                  <FigureCard key={ef.ref_id} ef={ef} />
                ))}
              </div>
            )}
          </div>
        </details>
      )}
    </>
  );
}

function FiguresBody({
  section,
  figuresData,
  bookId,
  onFiguresRefetch,
}: {
  section: Section;
  figuresData: BookFigures | null;
  bookId: string | undefined;
  onFiguresRefetch: () => void;
}) {
  const sectionFigures =
    figuresData?.sections.find((s) => s.section_ref === section.section_id) ?? null;
  const [regenModalOpen, setRegenModalOpen] = useState(false);
  const [regenInstruction, setRegenInstruction] = useState('');
  const [regenStatus, setRegenStatus] = useState<string | null>(null);
  const [regenBusy, setRegenBusy] = useState(false);

  const triggerRegen = async () => {
    if (!bookId) return;
    setRegenBusy(true);
    setRegenStatus('Queueing…');
    try {
      await regenerateSectionFigures(bookId, section.section_id, {
        custom_instructions: regenInstruction || null,
      });
      setRegenStatus('✓ Figure regeneration queued — refreshing soon');
      setRegenModalOpen(false);
      setRegenInstruction('');
      setTimeout(() => {
        onFiguresRefetch();
        setRegenStatus(null);
      }, 2000);
    } catch (e) {
      setRegenStatus(e instanceof Error ? e.message : 'Failed to start figure regen');
    } finally {
      setRegenBusy(false);
    }
  };

  return (
    <>
      <div style={{ padding: '12px 24px 0 24px', display: 'flex', gap: 8, alignItems: 'center' }}>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => setRegenModalOpen(true)}
          title="Regenerate this section's figures with a custom instruction"
          style={{ padding: '6px 12px', fontSize: 12 }}
        >
          <Icon name="regen" size={12} /> Reseed figures for this section
        </button>
        {regenStatus && (
          <div style={{ fontSize: 12, color: 'var(--ink-600)' }}>{regenStatus}</div>
        )}
      </div>

      <FiguresView
        sectionRef={section.section_id}
        sectionFigures={sectionFigures}
      />

      {regenModalOpen && (
        <div
          role="dialog"
          aria-modal="true"
          style={{
            position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)',
            zIndex: 60, display: 'grid', placeItems: 'center', padding: 24,
          }}
          onClick={() => setRegenModalOpen(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: 'var(--surface)', borderRadius: 12, padding: 24,
              maxWidth: 560, width: '100%', boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
            }}
          >
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>
              <Icon name="regen" size={16} /> Reseed figures for this section
            </div>
            <div style={{ fontSize: 13, color: 'var(--ink-600)', marginBottom: 4 }}>
              Section: <strong>{section.title || section.section_id}</strong>
            </div>
            <div style={{ fontSize: 13, color: 'var(--ink-500)', marginBottom: 14 }}>
              Optional: add a custom instruction for how to regenerate the figures
              (e.g. cleaner labels, different style).
            </div>
            <textarea
              value={regenInstruction}
              onChange={(e) => setRegenInstruction(e.target.value)}
              rows={5}
              style={{
                width: '100%', padding: 10, border: '1px solid var(--line)',
                borderRadius: 8, fontSize: 13, fontFamily: 'inherit', resize: 'vertical',
              }}
              placeholder="e.g. simpler line drawings, label all axes clearly, remove watermark"
              autoFocus
            />
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 14 }}>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setRegenModalOpen(false)}
                disabled={regenBusy}
              >
                Cancel
              </button>
              <button
                className="btn btn-primary btn-sm"
                onClick={() => void triggerRegen()}
                disabled={regenBusy}
              >
                {regenBusy ? (
                  <>
                    <span className="spinner" /> Starting…
                  </>
                ) : (
                  <>
                    <Icon name="regen" size={12} /> Regenerate figures
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

// ─────────────────────────────────────────────────────────────────
// WordRatioBadge — green/amber/red pill showing regen/orig word ratio.
// ─────────────────────────────────────────────────────────────────

function WordRatioBadge({ ratio }: { ratio: number }) {
  const pct = Math.round(ratio * 100);
  const color =
    ratio >= 0.85 && ratio <= 1.15 ? 'var(--success)'
    : ratio >= 0.7 && ratio <= 1.3 ? 'var(--warning, #C28000)'
    : 'var(--red-600)';
  return (
    <span
      style={{
        fontSize: 10,
        fontWeight: 700,
        padding: '2px 8px',
        borderRadius: 10,
        background: 'var(--bg-tint)',
        color,
      }}
      title="Regenerated length as a percentage of original. Green = healthy (85-115%), amber = somewhat off (70-130%), red = drift outside ±30%."
    >
      {pct}% length
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────
// ReseedModal — opens a small text input to regenerate ONE section with
// a custom instruction. Backend already supports per-section retry.
// ─────────────────────────────────────────────────────────────────

function ReseedModal({
  sectionTitle,
  onSubmit,
  onClose,
}: {
  sectionTitle: string;
  onSubmit: (instruction: string) => Promise<void> | void;
  onClose: () => void;
}) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      // AWAIT — keeps the dialog showing the spinner while Gemini runs
      // (can take 20-60s). Without await the spinner flashed off
      // immediately and the user thought the click did nothing.
      await onSubmit(text.trim());
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div
        onClick={onClose}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(15,23,42,0.45)',
          zIndex: 200,
        }}
      />
      <div
        className="card fade-up"
        style={{
          position: 'fixed',
          top: '15vh',
          left: '50%',
          transform: 'translateX(-50%)',
          width: 'min(560px, 92vw)',
          padding: 22,
          zIndex: 201,
          boxShadow: 'var(--sh-pop)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
          <Icon name="regen" size={16} />
          <h3 style={{ fontSize: 16, fontWeight: 800, margin: 0 }}>
            Reseed this section
          </h3>
        </div>
        <div style={{ fontSize: 12, color: 'var(--ink-500)', marginBottom: 4 }}>
          Section: <strong>{sectionTitle}</strong>
        </div>
        <div style={{ fontSize: 13, color: 'var(--ink-700)', marginBottom: 10 }}>
          Add a custom instruction. The current global parameters still apply;
          this instruction is layered on top.
        </div>
        <textarea
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="e.g., Add a real-world analogy. Use simpler vocabulary. Re-derive the key equation step-by-step."
          rows={5}
          style={{
            width: '100%',
            padding: 10,
            border: '1px solid var(--line)',
            borderRadius: 8,
            font: 'inherit',
            fontSize: 13,
            color: 'var(--ink-900)',
            resize: 'vertical',
          }}
        />
        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 8,
            marginTop: 14,
          }}
        >
          <button className="btn btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            onClick={() => void submit()}
            disabled={!text.trim() || busy}
          >
            {busy ? <span className="spinner" /> : <Icon name="regen" size={14} />}
            {busy ? 'Regenerating… (this can take 20-60s)' : 'Regenerate this section'}
          </button>
        </div>
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────
// PreviewModal — full-screen clean view of one section's content.
// ─────────────────────────────────────────────────────────────────

function PreviewModal({
  section,
  regenBlocks,
  subTab,
  onClose,
}: {
  section: Section;
  regenBlocks: Array<{ t: string; [k: string]: unknown }> | undefined;
  subTab: SubTab;
  onClose: () => void;
}) {
  const hasRegen = Array.isArray(regenBlocks) && regenBlocks.length > 0;
  const blocks = subTab === 'original' || !hasRegen ? undefined : regenBlocks;
  return (
    <>
      <div
        onClick={onClose}
        style={{
          position: 'fixed',
          inset: 0,
          background: 'rgba(15,23,42,0.65)',
          zIndex: 200,
        }}
      />
      <div
        className="fade-up"
        style={{
          position: 'fixed',
          top: '5vh',
          bottom: '5vh',
          left: '50%',
          transform: 'translateX(-50%)',
          width: 'min(900px, 94vw)',
          background: '#faf8f4',
          borderRadius: 12,
          padding: 28,
          zIndex: 201,
          boxShadow: 'var(--sh-pop)',
          overflowY: 'auto',
        }}
      >
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            marginBottom: 20,
            borderBottom: '1px solid var(--line)',
            paddingBottom: 14,
          }}
        >
          <div style={{ flex: 1 }}>
            <div
              style={{
                fontSize: 11,
                fontWeight: 700,
                letterSpacing: '0.1em',
                textTransform: 'uppercase',
                color: 'var(--indigo-700)',
                marginBottom: 4,
              }}
            >
              {subTab === 'original' || !hasRegen ? 'Original' : '✨ Regenerated'} · Preview
            </div>
            <h2 style={{ margin: 0, fontSize: 22, fontWeight: 800 }}>
              {section.title || section.section_id}
            </h2>
          </div>
          <button className="btn btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <TheoryView
          section={section}
          blocksOverride={(blocks ?? null) as Array<{ t: string; [k: string]: unknown }> | null}
          banner={blocks ? { label: 'Regenerated', tone: 'regen' } : { label: 'Original', tone: 'original' }}
          hideHeader
          flat
        />
      </div>
    </>
  );
}
