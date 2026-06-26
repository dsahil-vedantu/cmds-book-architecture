// Mock theory / questions / figures for the chapter review page. Ported
// verbatim from /tmp/vstudio-design/cmds/project/pages-chapter.jsx.
//
// In Phase 4 the chapter page reaches into the backend's `final_merge` and
// `final_draft` endpoints instead.

export type TheorySection = {
  id: string;
  heading: string;
  body: string;
  regen?: boolean;
};

export const ORIGINAL_THEORY: TheorySection[] = [
  {
    id: 's1',
    heading: '3.1 The Graphical Method',
    body: 'The method of solving the quadratic equation of the form ax² + bx + c = 0 by drawing the graph of the corresponding quadratic function is called the graphical method. Here, the points where the parabola y = ax² + bx + c intersects the x-axis give the real roots of the equation.',
  },
  {
    id: 's2',
    heading: '3.2 Drawing the Parabola',
    body: 'In order to draw the graph, we first construct a table of values by substituting suitable values of x and computing the corresponding values of y. The points so obtained are plotted on the cartesian plane and joined freehand to form a smooth curve. This curve is the parabola.',
  },
  {
    id: 's3',
    heading: '3.3 Interpreting Roots',
    body: 'If the parabola crosses the x-axis at two points, the equation has two distinct real roots. If it just touches the x-axis at one point, the equation has two equal real roots. If it does not intersect the x-axis at all, the equation has no real roots; the roots are imaginary in nature.',
  },
];

export const REGEN_THEORY: TheorySection[] = [
  {
    id: 's1',
    heading: '3.1 The Graphical Method',
    body: 'To solve a quadratic equation ax² + bx + c = 0 graphically, we plot the curve y = ax² + bx + c on a cartesian plane and locate where it cuts the x-axis. These x-intercepts are precisely the real roots of the equation — a visual one-to-one match with the algebraic solution.',
    regen: true,
  },
  {
    id: 's2',
    heading: '3.2 Drawing the Parabola',
    body: 'Start by making a table: choose 5–7 values of x around the vertex, compute y for each, and you have your points. Plot them carefully and join them with a smooth curve — that curve is your parabola. A neat plot is half the answer; rushed plots hide the roots.',
    regen: true,
  },
  {
    id: 's3',
    heading: '3.3 Interpreting Roots',
    body: 'Read the graph: two crossings on the x-axis → two distinct real roots; a single touch (vertex on the x-axis) → equal roots; no contact at all → no real roots (the roots are imaginary). This visual rule mirrors what the discriminant tells you algebraically.',
    regen: true,
  },
];

export type DiffOp = { type: 'add' | 'del' | 'eq'; text: string };

export const DIFF_TEXT: DiffOp[] = [
  { type: 'del', text: 'The method of solving the quadratic equation of the form ax² + bx + c = 0 by drawing the graph of the corresponding quadratic function ' },
  { type: 'add', text: 'To solve a quadratic equation ax² + bx + c = 0 graphically, we plot the curve y = ax² + bx + c on a cartesian plane and locate where it cuts the x-axis. These x-intercepts ' },
  { type: 'eq',  text: 'are ' },
  { type: 'del', text: 'called the graphical method. Here, the points where the parabola y = ax² + bx + c intersects the x-axis give ' },
  { type: 'add', text: 'precisely ' },
  { type: 'eq',  text: 'the real roots of the equation' },
  { type: 'add', text: ' — a visual one-to-one match with the algebraic solution' },
  { type: 'eq',  text: '.' },
];

export type QuestionType = 'Short answer' | 'Long answer' | 'MCQ' | 'Assertion–Reason';
export type QuestionDifficulty = 'Easy' | 'Medium' | 'Hard';

export type DemoQuestion = {
  n: number;
  type: QuestionType;
  diff: QuestionDifficulty;
  q: string;
  variants?: string[];
  answer?: string;
  options?: string[];
  correct?: number;
};

export const QUESTIONS_DEMO: DemoQuestion[] = [
  {
    n: 1,
    type: 'Short answer',
    diff: 'Easy',
    q: 'Find the roots of the quadratic equation x² − 5x + 6 = 0 graphically.',
    variants: [
      'Solve x² − 5x + 6 = 0 by plotting its graph and reading off the x-intercepts.',
      'Use the graphical method to determine the roots of x² − 5x + 6 = 0.',
    ],
    answer: 'Roots are x = 2 and x = 3.',
  },
  {
    n: 2,
    type: 'Long answer',
    diff: 'Medium',
    q: 'Draw the graph of y = 2x² − 3x − 2 and use it to find the solution of the equation 2x² − 3x − 2 = 0.',
    variants: [
      'Plot y = 2x² − 3x − 2 on a graph paper and from the curve identify the values of x where 2x² − 3x − 2 = 0.',
      'By drawing the parabola y = 2x² − 3x − 2, determine where it cuts the x-axis to solve 2x² − 3x − 2 = 0.',
    ],
    answer: 'Roots are x = 2 and x = −0.5.',
  },
  {
    n: 3,
    type: 'MCQ',
    diff: 'Easy',
    q: 'A quadratic equation has two equal roots when its parabola:',
    options: [
      'Cuts the x-axis at two points',
      'Touches the x-axis at exactly one point',
      'Does not intersect the x-axis',
      'Crosses the y-axis at the origin',
    ],
    correct: 1,
    variants: ['For a quadratic with two equal roots, the parabola y = ax² + bx + c:'],
  },
  {
    n: 4,
    type: 'Assertion–Reason',
    diff: 'Hard',
    q: 'Assertion (A): If the parabola y = ax² + bx + c does not intersect the x-axis, the equation has no real roots.\nReason (R): The discriminant b² − 4ac is negative in this case.',
    options: [
      'Both A and R are true and R is the correct explanation of A',
      'Both A and R are true but R is not the correct explanation',
      'A is true, R is false',
      'A is false, R is true',
    ],
    correct: 0,
  },
];
