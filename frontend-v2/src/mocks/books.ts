// Mock data ported from the design bundle (cmds/project/data.jsx).
// Real fetches replace these in Phase 4 (wire-to-backend).

export type BookStatus = 'done' | 'processing' | 'queued';

export type Book = {
  id: string;
  title: string;
  subject: string;
  grade: string;
  template: string;
  chapters: number;
  questions: number;
  figures: number;
  status: BookStatus;
  progress: number;
  stage?: string;
  updated: string;
  cover: keyof typeof COVER_GRADIENTS;
  pages: number;
  folder_id?: string | null;
};

export const COVER_GRADIENTS = {
  indigo: 'linear-gradient(135deg, #1A237E 0%, #3F4AB0 100%)',
  red:    'linear-gradient(135deg, #C73824 0%, #E94B35 60%, #FF8A72 100%)',
  teal:   'linear-gradient(135deg, #0E7C6B 0%, #14B8A6 100%)',
  amber:  'linear-gradient(135deg, #B45309 0%, #F59E0B 100%)',
  violet: 'linear-gradient(135deg, #5B21B6 0%, #8B5CF6 100%)',
  green:  'linear-gradient(135deg, #047857 0%, #10B981 100%)',
} as const;

export const MOCK_BOOKS: Book[] = [
  { id: 'b1', title: 'Quadratic Equations', subject: 'Mathematics', grade: 'Class 10', template: 'CBSE', chapters: 12, questions: 384, figures: 47, status: 'done', progress: 100, updated: '2 hours ago', cover: 'indigo', pages: 218 },
  { id: 'b2', title: 'Class 7 Mathematics', subject: 'Mathematics', grade: 'Class 7', template: 'CBSE', chapters: 15, questions: 512, figures: 89, status: 'processing', progress: 65, stage: 'Regenerating theory · Ch 3/12', updated: 'just now', cover: 'red', pages: 296 },
  { id: 'b3', title: 'Physics for JEE Main', subject: 'Physics', grade: 'Class 11–12', template: 'JEE', chapters: 22, questions: 1240, figures: 156, status: 'done', progress: 100, updated: 'yesterday', cover: 'teal', pages: 612 },
  { id: 'b4', title: 'Organic Chemistry — NEET', subject: 'Chemistry', grade: 'Class 12', template: 'NEET', chapters: 18, questions: 920, figures: 234, status: 'done', progress: 100, updated: '3 days ago', cover: 'amber', pages: 488 },
  { id: 'b5', title: 'Trigonometry Foundations', subject: 'Mathematics', grade: 'Class 11', template: 'CBSE', chapters: 8, questions: 256, figures: 38, status: 'queued', progress: 0, updated: '5 min ago', cover: 'violet', pages: 184 },
  { id: 'b6', title: 'Living World — Biology', subject: 'Biology', grade: 'Class 11', template: 'NEET', chapters: 14, questions: 612, figures: 312, status: 'done', progress: 100, updated: 'last week', cover: 'green', pages: 372 },
];
