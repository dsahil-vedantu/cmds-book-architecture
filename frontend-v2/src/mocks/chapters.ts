// Per-book chapter lists + pipeline stage labels. Ported from data.jsx.

export type ChapterStatus = 'done' | 'processing' | 'queued';

export type Chapter = {
  id: string;
  n: number;
  title: string;
  status: ChapterStatus;
  sections: number;
  questions: number;
  figures: number;
  progress?: number;
};

const CHAPTERS_B1: Chapter[] = [
  { id: 'q1', n: 1, title: 'Introduction',                       status: 'done', sections: 6,  questions: 24, figures: 3 },
  { id: 'q2', n: 2, title: 'Solution of a Quadratic Equation',   status: 'done', sections: 9,  questions: 42, figures: 5 },
  { id: 'q3', n: 3, title: 'Graphical Method',                   status: 'done', sections: 10, questions: 45, figures: 8 },
  { id: 'q4', n: 4, title: 'Nature of Roots (Discriminant)',     status: 'done', sections: 7,  questions: 38, figures: 4 },
  { id: 'q5', n: 5, title: 'Quadratic Formula',                  status: 'done', sections: 8,  questions: 51, figures: 3 },
  { id: 'q6', n: 6, title: 'Applications in Word Problems',      status: 'done', sections: 12, questions: 67, figures: 9 },
  { id: 'q7', n: 7, title: 'Higher Order Equations',             status: 'done', sections: 9,  questions: 44, figures: 6 },
  { id: 'q8', n: 8, title: 'Review & Practice',                  status: 'done', sections: 5,  questions: 73, figures: 9 },
];

const CHAPTERS_B2: Chapter[] = [
  { id: 'c1', n: 1, title: 'Introduction to Integers',           status: 'done',       sections: 8,  questions: 42, figures: 6 },
  { id: 'c2', n: 2, title: 'Fractions and Decimals',             status: 'done',       sections: 11, questions: 58, figures: 12 },
  { id: 'c3', n: 3, title: 'Data Handling',                      status: 'processing', sections: 9,  questions: 38, figures: 8, progress: 65 },
  { id: 'c4', n: 4, title: 'Simple Equations',                   status: 'queued',     sections: 7,  questions: 33, figures: 4 },
  { id: 'c5', n: 5, title: 'Lines and Angles',                   status: 'queued',     sections: 10, questions: 41, figures: 18 },
  { id: 'c6', n: 6, title: 'The Triangle and its Properties',    status: 'queued',     sections: 9,  questions: 36, figures: 14 },
  { id: 'c7', n: 7, title: 'Congruence of Triangles',            status: 'queued',     sections: 8,  questions: 29, figures: 11 },
  { id: 'c8', n: 8, title: 'Comparing Quantities',               status: 'queued',     sections: 10, questions: 44, figures: 5 },
];

export const BOOK_CHAPTERS: Record<string, Chapter[]> = {
  b1: CHAPTERS_B1,
  b2: CHAPTERS_B2,
};

export type PipelineStage = { id: string; label: string; mono: string };

export const PIPELINE_STAGES: PipelineStage[] = [
  { id: 's1', label: 'PDF analysis',           mono: 'analyse' },
  { id: 's2', label: 'Schema build',           mono: 'schema' },
  { id: 's3', label: 'Theory extraction',      mono: 'extract.theory' },
  { id: 's4', label: 'Question extraction',    mono: 'extract.questions' },
  { id: 's5', label: 'Figure extraction',      mono: 'extract.figures' },
  { id: 's6', label: 'Theory regeneration',    mono: 'regen.theory' },
  { id: 's7', label: 'Question regeneration',  mono: 'regen.questions' },
];

export function getChapters(bookId: string): Chapter[] {
  return BOOK_CHAPTERS[bookId] ?? [];
}

export function getChapter(bookId: string, chapterId: string): Chapter | undefined {
  return getChapters(bookId).find((c) => c.id === chapterId);
}
