// Mock data for the New Book / Upload flow. Ported from
// /tmp/vstudio-design/cmds/project/pages-upload.jsx.

export type BookFolder = {
  id: string;
  name: string;
  count: number;
  color: string;
};

export const FOLDERS: BookFolder[] = [
  { id: 'cbse-10', name: 'CBSE Class 10',    count: 4, color: '#1A237E' },
  { id: 'jee',     name: 'JEE Preparation',  count: 2, color: '#E94B35' },
  { id: 'neet',    name: 'NEET Foundation',  count: 3, color: '#10B981' },
  { id: 'class-7', name: 'Class 7 Master',   count: 6, color: '#8B5CF6' },
  { id: 'drafts',  name: 'Drafts & Trials',  count: 1, color: '#94A3B8' },
];

export type SchemaChapter = {
  n: number;
  title: string;
  sections: number;
  questions: number;
  figures: number;
};

export type ExtractedSchema = {
  pages: number;
  chapters: SchemaChapter[];
};

export const EXTRACTED_SCHEMA: ExtractedSchema = {
  pages: 218,
  chapters: [
    { n: 1, title: 'Introduction to Quadratic Equations',  sections: 6,  questions: 24, figures: 3 },
    { n: 2, title: 'Standard Form & Roots',                sections: 9,  questions: 42, figures: 5 },
    { n: 3, title: 'Graphical Method',                     sections: 10, questions: 45, figures: 8 },
    { n: 4, title: 'Nature of Roots (Discriminant)',       sections: 7,  questions: 38, figures: 4 },
    { n: 5, title: 'Quadratic Formula',                    sections: 8,  questions: 51, figures: 3 },
    { n: 6, title: 'Applications in Word Problems',        sections: 12, questions: 67, figures: 9 },
    { n: 7, title: 'Higher Order Equations',               sections: 9,  questions: 44, figures: 6 },
    { n: 8, title: 'Review & Practice',                    sections: 5,  questions: 73, figures: 9 },
  ],
};

export type ExtractStage = { id: string; label: string; detail: string };

export const EXTRACT_STAGES: ExtractStage[] = [
  { id: 'analyse',   label: 'Analyzing PDF structure', detail: 'pages, layout, columns' },
  { id: 'schema',    label: 'Building chapter schema', detail: 'detected chapter boundaries' },
  { id: 'theory',    label: 'Extracting theory',       detail: 'sections, paragraphs, formulas' },
  { id: 'questions', label: 'Extracting questions',    detail: 'short, long, MCQ, assertion' },
  { id: 'figures',   label: 'Extracting figures',      detail: 'images, diagrams, captions' },
];

export type RegenStage = { id: string; label: string; mono: string };

export const REGEN_STAGES: RegenStage[] = [
  { id: 'theory', label: 'Regenerating theory',    mono: 'regen.theory' },
  { id: 'qs',     label: 'Regenerating questions', mono: 'regen.questions' },
  { id: 'figs',   label: 'Regenerating figures',   mono: 'regen.figures' },
  { id: 'merge',  label: 'Composing final draft',  mono: 'merge.draft' },
];

// Lightweight "picked file" shape — the actual File object from the input
// gets boiled down to this for the mock state.
export type PickedFile = {
  name: string;
  size: number;
  pages?: number;
};
