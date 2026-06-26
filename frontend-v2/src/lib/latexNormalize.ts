// latexNormalize — single, deterministic pre-processor that turns any
// LaTeX / markup-bearing text into something the shared MathMarkdown
// renderer (react-markdown + remark-math + KaTeX + mhchem) can render.
//
// Architecture: this is the ONE place that decides "what is math / code /
// table / formatting" in raw block text. It is baked into MathMarkdown, so
// every caller (theory blocks, definitions, lists, key points, equations,
// question text + solutions, table cells, headings, custom text) gets the
// same treatment — no per-block, per-call handling.
//
// Design rule: CONSERVATIVE. Only transform when there's an unambiguous
// LaTeX signal. Plain prose must pass through untouched (no false-positive
// math-italicising of normal sentences).

// Real LaTeX math command/structure tokens. Presence of any of these in a
// run of text is a strong signal it's math (used to decide whether to wrap
// an un-delimited fragment in $...$).
const MATH_TOKEN =
  /\\(?:frac|dfrac|tfrac|sqrt|int|iint|oint|sum|prod|lim|infty|partial|nabla|times|cdot|div|pm|mp|leq|geq|neq|approx|equiv|propto|rightarrow|leftarrow|Rightarrow|hat|vec|bar|tilde|dot|ddot|overline|underline|alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|phi|omega|Delta|Sigma|Omega|Phi|Theta|begin\{(?:array|matrix|pmatrix|bmatrix|cases|aligned)\})|[\^_]\{|\^[0-9A-Za-z]|_[0-9A-Za-z]/;

// Escape a string for use inside a RegExp.
function esc(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Replace \begin{ENV}...\end{ENV} bodies via a callback. Handles the first
// level only (textbook content doesn't nest these).
function replaceEnv(
  text: string,
  env: string,
  fn: (body: string, optArg: string) => string,
): string {
  const re = new RegExp(
    `\\\\begin\\{${esc(env)}\\}(\\{[^}]*\\}|\\[[^\\]]*\\])?([\\s\\S]*?)\\\\end\\{${esc(env)}\\}`,
    'g',
  );
  return text.replace(re, (_m, optArg = '', body = '') =>
    fn(String(body), String(optArg || '')),
  );
}

// LaTeX \begin{tabular}{...} ... \end{tabular} → GFM markdown table.
// Cells split on '&', rows on '\\'. \hline / booktabs rules are dropped.
function tabularToMarkdown(body: string): string {
  const rows = body
    .replace(/\\hline|\\toprule|\\midrule|\\bottomrule|\\cline\{[^}]*\}/g, '')
    .split(/\\\\/)
    .map((r) => r.trim())
    .filter((r) => r.length > 0);
  if (rows.length === 0) return '';
  const cells = rows.map((r) =>
    r.split('&').map((c) => c.trim().replace(/\s+/g, ' ')),
  );
  const cols = Math.max(...cells.map((c) => c.length));
  const pad = (arr: string[]) => {
    const a = arr.slice();
    while (a.length < cols) a.push('');
    return a;
  };
  const out: string[] = [];
  out.push('| ' + pad(cells[0]).join(' | ') + ' |');
  out.push('|' + Array(cols).fill('---').join('|') + '|');
  for (const row of cells.slice(1)) out.push('| ' + pad(row).join(' | ') + ' |');
  // Surround with blank lines so GFM parses it as a table block.
  return '\n\n' + out.join('\n') + '\n\n';
}

export function normalizeLatex(raw: string): string {
  if (typeof raw !== 'string' || !raw) return raw || '';
  let s = raw;

  // 1. Code / verbatim environments → fenced code block (before anything
  //    else, so their contents are never mangled by later passes).
  s = replaceEnv(s, 'lstlisting', (body) => `\n\n\`\`\`\n${body.replace(/^\n+|\n+$/g, '')}\n\`\`\`\n\n`);
  s = replaceEnv(s, 'verbatim', (body) => `\n\n\`\`\`\n${body.replace(/^\n+|\n+$/g, '')}\n\`\`\`\n\n`);

  // 2. LaTeX tables → markdown tables.
  s = replaceEnv(s, 'tabular', (body) => tabularToMarkdown(body));
  s = replaceEnv(s, 'array', (body) => tabularToMarkdown(body)); // text-mode array fallback

  // 3. Display math environments → $$...$$ (KaTeX renders align/aligned/etc).
  for (const env of ['align', 'align*', 'equation', 'equation*', 'aligned', 'gather', 'gather*']) {
    s = replaceEnv(s, env, (body) => `\n\n$$\\begin{aligned}${body}\\end{aligned}$$\n\n`);
  }

  // 4. LaTeX text-formatting commands → markdown (only outside $...$ — we
  //    apply globally but these tokens don't appear inside real math).
  s = s.replace(/\\textbf\{([^}]*)\}/g, '**$1**');
  s = s.replace(/\\(?:textit|emph)\{([^}]*)\}/g, '*$1*');
  s = s.replace(/\\texttt\{([^}]*)\}/g, '`$1`');
  s = s.replace(/\\verb\|([^|]*)\|/g, '`$1`');
  s = s.replace(/\\underline\{([^}]*)\}/g, '$1');

  // 5 + 6. FRAGMENT-LEVEL math wrapping. Walk the text; keep any existing
  //   $...$ segments verbatim; in the gaps between them, find math FRAGMENTS
  //   (runs of math tokens) and wrap each in $...$. This makes math render
  //   wherever it appears — fully bare lines, bare formulas mid-prose, AND
  //   partially-delimited lines (e.g. "$3x^2$ + 3x - 6 = 0") — WITHOUT ever
  //   wrapping prose words (variables are single chars / signal-bearing;
  //   words of 2+ pure letters like "by", "set", "equation" are never math).
  s = wrapMathInGaps(s);

  return s;
}

// True if a single whitespace-delimited token is a math token:
//   - a single alphanumeric char (a variable like x, or a digit), OR
//   - a token carrying a math signal (^ _ \ digit, or pure operators),
//   - and containing NO run of 2+ consecutive letters (so words like
//     "by", "of", "set", "equation", "sin" are NEVER math tokens).
function isMathToken(tok: string): boolean {
  if (!tok) return false;
  // 3+ consecutive letters (ignoring \commands) = a real word (increases,
  // equation, set) → never math. 2-letter runs (mv, KE) are allowed ONLY
  // when a math signal is present (handled below), so "mv^2" is math but
  // "by"/"of" are not.
  if (/[A-Za-z]{3,}/.test(tok.replace(/\\[a-zA-Z]+/g, ''))) return false;
  // single variable / digit / Greek letter (α, β, θ, ω …)
  if (/^[A-Za-z0-9Ͱ-Ͽ]$/.test(tok)) return true;
  // math charset: letters, digits, Greek, math-unicode (× ÷ ± ≤ ≥ → ⇌ ∑ ∫),
  // operators, braces, backslash.
  return /^[\\A-Za-z0-9^_{}()+\-=*/.,|<>[\]'’°±·×÷Ͱ-Ͽ⁰-₟←-⇿∀-⋿]+$/.test(tok) &&
    /[\\^_=+\-*/0-9{}±×÷←-⇿∀-⋿]/.test(tok); // signal
}

// A fragment qualifies for wrapping only if it carries a STRONG math signal
// (sub/superscript, a LaTeX command, or an operator between operands) — a lone
// variable or number is left as prose.
function fragmentIsMath(frag: string): boolean {
  return /[\^_]|\\[a-zA-Z]+|[A-Za-z0-9Ͱ-Ͽ]\s*[+\-*/=]\s*[A-Za-z0-9\\Ͱ-Ͽ]|\\(?:frac|sqrt|sum|int)|[±×÷←-⇿∀-⋿]/.test(frag);
}

// Last non-whitespace token currently buffered ends with a math operator?
// Used to decide whether a short alpha token is a variable operand (math) or
// a stray English word.
function bufEndsWithOperator(buf: string[]): boolean {
  for (let i = buf.length - 1; i >= 0; i--) {
    const t = buf[i];
    if (/^\s+$/.test(t)) continue;
    return /[+\-*/=^_]$/.test(t);
  }
  return false;
}

// A short pure-alpha token (≤3 letters, optional trailing punctuation) that
// could be a multi-letter physics variable (at, mv, KE) — only treated as
// math when it directly follows an operator inside an active math run.
function isShortAlphaOperand(tok: string): boolean {
  return /^[A-Za-z]{2,3}[.,;:]?$/.test(tok);
}

function wrapFragmentsInProse(text: string): string {
  const tokens = text.split(/(\s+)/); // keep whitespace tokens
  const out: string[] = [];
  let buf: string[] = [];
  const flush = () => {
    if (!buf.length) return;
    let frag = buf.join('');
    // peel trailing whitespace back out of the fragment
    const tail = frag.match(/\s+$/)?.[0] ?? '';
    frag = frag.slice(0, frag.length - tail.length);
    if (frag && fragmentIsMath(frag)) out.push(`$${frag}$`);
    else out.push(frag);
    if (tail) out.push(tail);
    buf = [];
  };
  // Next non-whitespace token after index i (for lookahead).
  const nextReal = (i: number): string => {
    for (let j = i + 1; j < tokens.length; j++) {
      if (!/^\s+$/.test(tokens[j])) return tokens[j];
    }
    return '';
  };
  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i];
    if (/^\s+$/.test(tok)) {
      if (buf.length) buf.push(tok); // whitespace may sit inside a math run
      else out.push(tok);
    } else if (
      isMathToken(tok) ||
      // short alpha variable as an operand right AFTER an operator (at in u+at)
      (buf.length && bufEndsWithOperator(buf) && isShortAlphaOperand(tok)) ||
      // short alpha variable right BEFORE an operator (KE in KE = mv^2/2)
      (isShortAlphaOperand(tok) && /^[+\-*/=^_]/.test(nextReal(i)))
    ) {
      buf.push(tok);
    } else {
      flush();
      out.push(tok); // prose word / punctuation
    }
  }
  flush();
  return out.join('');
}

// Walk the string, preserving existing $...$ and \ce{...} segments, wrapping
// math fragments only in the prose gaps between them.
function wrapMathInGaps(s: string): string {
  // Tokenize into: existing $$...$$, $...$, \ce{...}, and plain gaps.
  const re = /\$\$[\s\S]*?\$\$|\$[^$]*\$|\\ce\{(?:[^{}]|\{[^{}]*\})*\}/g;
  let out = '';
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s)) !== null) {
    out += wrapFragmentsInProse(s.slice(last, m.index));
    const seg = m[0];
    // bare \ce{...} (not already in $) → wrap so mhchem renders it
    out += seg.startsWith('\\ce') ? `$${seg}$` : seg;
    last = m.index + seg.length;
  }
  out += wrapFragmentsInProse(s.slice(last));
  return out;
}
