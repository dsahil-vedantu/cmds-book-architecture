// Style templates used both by the Templates page and Settings → Defaults.

export type Template = {
  id: string;
  name: string;
  desc: string;
  tone: string;
  q: string;
  figs: string;
  grad: string;
  tag?: string;
  usage: string;
};

export const TEMPLATES: Template[] = [
  {
    id: 'cbse',
    name: 'CBSE Standard',
    desc: 'NCERT-style conversational theory, in-line worked examples, mixed-difficulty practice with subjective + objective.',
    tone: 'Conversational',
    q: 'Mixed',
    figs: 'Preserve + caption',
    grad: 'linear-gradient(135deg, #1A237E, #3F4AB0)',
    tag: 'Most used',
    usage: '64% of books',
  },
  {
    id: 'jee',
    name: 'JEE Advanced',
    desc: 'Concept-first theory with derivations, prominent formula boxes, JEE-pattern MCQs and assertion-reasoning.',
    tone: 'Rigorous',
    q: 'MCQ + AR',
    figs: 'AI redraw',
    grad: 'linear-gradient(135deg, #C73824, #E94B35)',
    usage: '22% of books',
  },
  {
    id: 'neet',
    name: 'NEET Foundations',
    desc: 'Diagram-heavy explanations, factual recall focus, NEET-pattern single-correct MCQs with answer keys.',
    tone: 'Factual',
    q: 'MCQ',
    figs: 'AI redraw + label',
    grad: 'linear-gradient(135deg, #047857, #10B981)',
    usage: '11% of books',
  },
  {
    id: 'custom',
    name: 'Custom',
    desc: 'Build your own — pick tone, question types, depth, and figure handling per section.',
    tone: 'You decide',
    q: 'You decide',
    figs: 'You decide',
    grad: 'linear-gradient(135deg, #5B21B6, #8B5CF6)',
    tag: 'New',
    usage: '3% of books',
  },
];
