/**
 * Port of backend's `_normalise_math_prose` (docx_export.py).
 * Converts LaTeX text → readable Unicode so the on-screen preview
 * matches the DOCX export byte-for-byte (Path A — Preview-as-DOCX).
 *
 * Used by RichText when its `unicodeMath` flag is on. Must stay in
 * sync with the Python equivalent — if you change one, change both.
 */

// ─── LaTeX command → Unicode (longer prefixes first) ───────────────────
const LATEX_TO_UNICODE: Array<[string, string]> = [
  ["\\Longleftrightarrow", "⟺"],
  ["\\Longrightarrow", "⟹"],
  ["\\Longleftarrow", "⟸"],
  ["\\Leftrightarrow", "⇔"],
  ["\\Rightarrow", "⇒"],
  ["\\Leftarrow", "⇐"],
  ["\\leftrightarrow", "↔"],
  ["\\rightarrow", "→"],
  ["\\leftarrow", "←"],
  ["\\therefore", "∴"],
  ["\\because", "∵"],
  ["\\approx", "≈"],
  ["\\equiv", "≡"],
  ["\\infty", "∞"],
  ["\\times", "×"],
  ["\\cdot", "·"],
  ["\\div", "÷"],
  ["\\pm", "±"],
  ["\\mp", "∓"],
  ["\\leq", "≤"],
  ["\\geq", "≥"],
  ["\\neq", "≠"],
  ["\\ne", "≠"],
  ["\\to", "→"],
  // Set theory
  ["\\cup", "∪"],
  ["\\cap", "∩"],
  ["\\subseteq", "⊆"],
  ["\\supseteq", "⊇"],
  ["\\subset", "⊂"],
  ["\\supset", "⊃"],
  ["\\notin", "∉"],
  ["\\in", "∈"],
  ["\\emptyset", "∅"],
  ["\\varnothing", "∅"],
  // Logic
  ["\\forall", "∀"],
  ["\\exists", "∃"],
  ["\\lnot", "¬"],
  ["\\neg", "¬"],
  ["\\land", "∧"],
  ["\\lor", "∨"],
  // Misc
  ["\\partial", "∂"],
  ["\\nabla", "∇"],
  ["\\sum", "∑"],
  ["\\prod", "∏"],
  ["\\int", "∫"],
  ["\\oint", "∮"],
  ["\\ldots", "…"],
  ["\\cdots", "⋯"],
  // Greek (most common)
  ["\\alpha", "α"], ["\\beta", "β"], ["\\gamma", "γ"],
  ["\\delta", "δ"], ["\\epsilon", "ε"], ["\\theta", "θ"],
  ["\\lambda", "λ"], ["\\mu", "μ"], ["\\pi", "π"],
  ["\\sigma", "σ"], ["\\phi", "φ"], ["\\omega", "ω"],
  ["\\Delta", "Δ"], ["\\Theta", "Θ"], ["\\Lambda", "Λ"],
  ["\\Sigma", "Σ"], ["\\Phi", "Φ"], ["\\Omega", "Ω"],
  // Spacing — drop the silent forms
  ["\\,", " "], ["\\;", " "], ["\\:", " "], ["\\!", ""],
];

// ─── Super/subscript translation tables ──────────────────────────────────
const SUP_MAP: Record<string, string> = {
  "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
  "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
  "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
  n: "ⁿ", i: "ⁱ",
};
const SUB_MAP: Record<string, string> = {
  "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
  "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
  "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎",
  n: "ₙ", i: "ᵢ", a: "ₐ", e: "ₑ", o: "ₒ", x: "ₓ",
};

function tryTranslate(
  body: string,
  table: Record<string, string>,
): string | null {
  // Only translate if EVERY char has a mapping — otherwise return null
  // (caller leaves the LaTeX form intact)
  const out: string[] = [];
  for (const ch of body) {
    if (!(ch in table)) return null;
    out.push(table[ch]);
  }
  return out.join("");
}

/**
 * Apply LaTeX → Unicode normalisation. Mirrors `_normalise_math_prose`
 * in app/services/docx_export.py.
 */
export function normaliseMathProse(text: string): string {
  if (!text) return text;
  let s = text;

  // 1. Drop \text{X} / \mathrm{X} / etc. wrappers — keep inner content
  s = s.replace(
    /\\(?:text|mathrm|mathbf|mathit|operatorname)\{([^{}]*)\}/g,
    (_m, inner) => inner,
  );
  s = s.replace(/\\left\b/g, "");
  s = s.replace(/\\right\b/g, "");

  // 2. Fixpoint loop covering super/subscripts + sqrt + frac
  //    Order matters: subs/sups first so `\sqrt{b^{2}-4ac}` becomes
  //    `\sqrt{b²-4ac}` (no inner braces) before sqrt regex tries to match.
  for (let iter = 0; iter < 8; iter++) {
    const before = s;

    // Super/subscripts
    s = s.replace(
      /\^\{([^{}]{1,4})\}|\^([0-9a-zA-Z+\-=()])/g,
      (_m, g1, g2) => {
        const body = g1 ?? g2 ?? "";
        const t = tryTranslate(body, SUP_MAP);
        return t ?? _m;
      },
    );
    s = s.replace(
      /_\{([^{}]{1,4})\}|_([0-9a-zA-Z+\-=()])/g,
      (_m, g1, g2) => {
        const body = g1 ?? g2 ?? "";
        const t = tryTranslate(body, SUB_MAP);
        return t ?? _m;
      },
    );

    // Roots
    s = s.replace(
      /\\sqrt\[([^\]]+)\]\{([^{}]*)\}/g,
      (_m, n, body) => {
        const sup =
          ({ "2": "²", "3": "³", "4": "⁴", "5": "⁵" } as Record<string, string>)[
            n.trim()
          ] ?? n.trim();
        return `${sup}√(${body})`;
      },
    );
    s = s.replace(/\\sqrt\{([^{}]*)\}/g, (_m, body) => `√(${body})`);

    // Fractions
    s = s.replace(
      /\\(?:d|t)?frac\{([^{}]*)\}\{([^{}]*)\}/g,
      (_m, num, den) => {
        const wrap = (x: string) =>
          x.length === 1 && /[A-Za-z0-9]/.test(x) ? x : `(${x})`;
        return `${wrap(num.trim())}/${wrap(den.trim())}`;
      },
    );

    if (s === before) break;
  }

  // 3. Map remaining LaTeX commands → Unicode
  for (const [tex, uni] of LATEX_TO_UNICODE) {
    s = s.split(tex).join(uni);
  }

  return s;
}

/**
 * Strip `$...$` wrappers from text after Unicode normalisation has run.
 * The math inside stays as-is (now already Unicode); the dollar signs
 * are noise in the DOCX-mirror preview because we render math as plain
 * italic text, not as a delimited math zone.
 */
export function stripMathDelimiters(text: string): string {
  if (!text) return text;
  // Display math: $$...$$ → \n<inner>\n
  let s = text.replace(/\$\$([\s\S]+?)\$\$/g, (_m, inner) => `\n${inner}\n`);
  // \[ ... \] display
  s = s.replace(/\\\[([\s\S]+?)\\\]/g, (_m, inner) => `\n${inner}\n`);
  // Inline $...$
  s = s.replace(/\$([^\n$]+?)\$/g, (_m, inner) => inner);
  // \( ... \) inline
  s = s.replace(/\\\(([^\n]+?)\\\)/g, (_m, inner) => inner);
  return s;
}
