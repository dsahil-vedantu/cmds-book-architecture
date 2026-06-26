import { createContext, useContext, useMemo } from "react";
import katex from "katex";
import "katex/dist/katex.min.css";
import { normaliseMathProse, stripMathDelimiters } from "./mathUnicode";

/**
 * RichText — renders OCR'd educational text with proper math + tables.
 *
 * Two render modes via RenderStyleContext:
 *   - "katex" (default) — math via KaTeX, pretty browser rendering
 *   - "unicode"          — math as italic Unicode text, mirroring the
 *                          DOCX export (so on-screen Preview matches the
 *                          downloaded file).
 *
 * Handles:
 *   - Inline math: $…$, \(…\)
 *   - Display math: $$…$$, \[…\]
 *   - Markdown pipe tables (`| col | col |\n|----|----|\n| a | b |`)
 *   - Plain paragraphs with preserved newlines
 */

export type RenderStyle = "katex" | "unicode";
export const RenderStyleContext = createContext<RenderStyle>("katex");

type Token =
  | { kind: "text"; value: string }
  | { kind: "math_inline"; tex: string }
  | { kind: "math_display"; tex: string }
  | { kind: "table"; headers: string[]; rows: string[][] };

const MATH_RE =
  /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\$([^\n$]+?)\$|\\\(([^\n]+?)\\\)/g;

function renderMath(tex: string, display: boolean): string {
  try {
    return katex.renderToString(tex, {
      displayMode: display,
      throwOnError: false,
      output: "html",
    });
  } catch {
    return display ? `$$${tex}$$` : `$${tex}$`;
  }
}

/** Detect a markdown table block in a chunk. Returns the parsed table and
 *  the [start, end) indices it consumed, or null. */
function tryParseTable(
  lines: string[],
  i: number,
): { headers: string[]; rows: string[][]; lastLine: number } | null {
  const header = lines[i];
  const sep = lines[i + 1];
  if (!header || !sep) return null;
  if (!header.includes("|")) return null;
  // Separator must be all dashes/colons/pipes/spaces
  if (!/^[\s|:\-]+$/.test(sep) || !sep.includes("-")) return null;
  const splitRow = (row: string) =>
    row
      .replace(/^\s*\|/, "")
      .replace(/\|\s*$/, "")
      .split("|")
      .map((c) => c.trim());
  const headers = splitRow(header);
  const rows: string[][] = [];
  let j = i + 2;
  while (j < lines.length && lines[j].includes("|") && lines[j].trim() !== "") {
    rows.push(splitRow(lines[j]));
    j++;
  }
  if (rows.length === 0) return null;
  return { headers, rows, lastLine: j - 1 };
}

function tokenize(text: string): Token[] {
  const out: Token[] = [];
  // First pass: extract tables (line-based)
  const lines = text.split(/\r?\n/);
  let i = 0;
  let buffer: string[] = [];
  const flushBuffer = () => {
    if (buffer.length === 0) return;
    const joined = buffer.join("\n");
    // Second pass: math inside buffered text
    let last = 0;
    MATH_RE.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = MATH_RE.exec(joined)) !== null) {
      if (m.index > last) {
        out.push({ kind: "text", value: joined.slice(last, m.index) });
      }
      const display = m[1] !== undefined || m[2] !== undefined;
      const tex = (m[1] ?? m[2] ?? m[3] ?? m[4] ?? "").trim();
      out.push({
        kind: display ? "math_display" : "math_inline",
        tex,
      });
      last = m.index + m[0].length;
    }
    if (last < joined.length) {
      out.push({ kind: "text", value: joined.slice(last) });
    }
    buffer = [];
  };
  while (i < lines.length) {
    const parsed = tryParseTable(lines, i);
    if (parsed) {
      flushBuffer();
      out.push({
        kind: "table",
        headers: parsed.headers,
        rows: parsed.rows,
      });
      i = parsed.lastLine + 1;
      continue;
    }
    buffer.push(lines[i]);
    i++;
  }
  flushBuffer();
  return out;
}

export function RichText({
  text,
  display = "block",
  fontSize,
}: {
  text: string;
  /** "block" preserves newlines (for paragraph/solution bodies);
   *  "inline" collapses whitespace. */
  display?: "block" | "inline";
  fontSize?: string;
}) {
  const style = useContext(RenderStyleContext);
  // Pre-process for the DOCX-mirror mode: convert LaTeX commands →
  // Unicode then strip $...$ delimiters. Math then renders as italic
  // text inline with the rest of the prose, matching what _DocBuilder
  // emits into the downloaded .docx.
  const processed = useMemo(() => {
    if (!text) return "";
    if (style === "unicode") {
      return stripMathDelimiters(normaliseMathProse(text));
    }
    return text;
  }, [text, style]);
  const tokens = useMemo(
    () => tokenize(processed || ""),
    [processed],
  );
  if (!text) return null;
  // In unicode mode there's no math token by definition (we pre-stripped
  // the delimiters), so tokenize() will yield only text/table chunks.
  return (
    <span
      style={{
        display: display === "block" ? "block" : "inline",
        whiteSpace: display === "block" ? "pre-wrap" : "normal",
        fontSize,
      }}
    >
      {tokens.map((t, i) => {
        if (t.kind === "text") return <span key={i}>{t.value}</span>;
        if (t.kind === "math_inline" || t.kind === "math_display") {
          if (style === "unicode") {
            // Render the (already Unicode-normalised) math as italic text
            return (
              <i
                key={i}
                style={{
                  display:
                    t.kind === "math_display" ? "block" : "inline",
                  margin: t.kind === "math_display" ? "6px 0" : undefined,
                  fontStyle: "italic",
                }}
              >
                {t.tex}
              </i>
            );
          }
          const html = renderMath(t.tex, t.kind === "math_display");
          return (
            <span
              key={i}
              style={{
                display: t.kind === "math_display" ? "block" : "inline-block",
                margin: t.kind === "math_display" ? "6px 0" : undefined,
              }}
              dangerouslySetInnerHTML={{ __html: html }}
            />
          );
        }
        // table
        return (
          <table
            key={i}
            style={{
              borderCollapse: "collapse",
              fontSize: "0.82rem",
              margin: "8px 0",
              width: "auto",
            }}
          >
            <thead>
              <tr>
                {t.headers.map((h, hi) => (
                  <th
                    key={hi}
                    style={{
                      border: "1px solid var(--border)",
                      padding: "4px 10px",
                      background: "var(--surface2)",
                      fontWeight: 600,
                      textAlign: "left",
                    }}
                  >
                    <RichText text={h} display="inline" />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {t.rows.map((row, ri) => (
                <tr key={ri}>
                  {row.map((cell, ci) => (
                    <td
                      key={ci}
                      style={{
                        border: "1px solid var(--border)",
                        padding: "4px 10px",
                        verticalAlign: "top",
                      }}
                    >
                      <RichText text={cell} display="inline" />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        );
      })}
    </span>
  );
}
