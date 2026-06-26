// MathMarkdown — single rendering primitive for any text that may
// contain markdown + LaTeX math. Used by:
//   - PreviewPage question solution_text
//   - PreviewPage theory blocks (t:'p', t:'eq', etc)
//   - Composer block previews
//
// Why centralized:
// Until now the preview rendered q.solution_text as raw <pre-wrap> which
// printed `$2x^2$` literally and `| X | 0 | 1 |` as raw pipes. Examples
// rendered fine because they had been pre-split into structured blocks.
// One renderer means questions and examples look identical regardless of
// how their content was sourced.

import ReactMarkdown from 'react-markdown';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';

// Important: katex CSS must be imported somewhere for math to render.
// We import it lazily here so it's pulled in only when MathMarkdown
// is first used in the bundle.
import 'katex/dist/katex.min.css';
// mhchem extension — enables `\ce{...}` chemistry formulas:
//   \ce{H2O}            → H₂O
//   \ce{CH3-CH2-OH}     → CH₃–CH₂–OH
//   \ce{Fe^{2+}}        → Fe²⁺
//   \ce{2H2 + O2 -> 2H2O}  → balanced reaction with arrow
// Side-effect import — registers \ce, \pu, \bond commands globally with KaTeX.
import 'katex/contrib/mhchem';

import { normalizeLatex } from '../lib/latexNormalize';

type Props = {
  children: string;
  inline?: boolean;
};

export function MathMarkdown({ children, inline = false }: Props) {
  // Don't crash on undefined/null input — render empty silently.
  const raw = typeof children === 'string' ? children : '';
  if (!raw.trim()) return null;
  // Single normalization point: any LaTeX/markup-bearing text — math, chem,
  // code, LaTeX tables, text-formatting, un-delimited inline math — is made
  // renderable here, so EVERY caller (theory blocks, defs, lists, key points,
  // equations, question text/solutions, table cells, headings) renders
  // identically. See lib/latexNormalize.
  const safe = normalizeLatex(raw);

  // ReactMarkdown wraps everything in <p> by default — for inline use
  // (e.g. inside a span) we strip the wrapping paragraph so it can sit
  // on the same line as surrounding text.
  const components = inline
    ? { p: ({ children }: any) => <>{children}</> }
    : undefined;

  return (
    <ReactMarkdown
      remarkPlugins={[remarkMath, remarkGfm]}
      // throwOnError:false + errorColor:inherit — when KaTeX hits an
      // unparseable expression (unbalanced braces, unknown command),
      // render the raw source in normal text color instead of the
      // default red error box. Source-faithful fallback beats a loud
      // red box for users skimming the doc.
      rehypePlugins={[[rehypeKatex, { throwOnError: false, errorColor: 'inherit', strict: 'ignore' }]]}
      components={components}
    >
      {safe}
    </ReactMarkdown>
  );
}
