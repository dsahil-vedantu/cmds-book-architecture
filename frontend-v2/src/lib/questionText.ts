// Helpers for rendering question text in the UI.
//
// The question extractor (Gemini) inserts `{{fig: <label> — <caption>}}`
// placeholders directly into question raw_text at the inline position
// where a figure visually appears. The actual figure renders separately
// via embedded_figures, so we strip the placeholder from the visible
// text — leaving it bleeds the literal token (e.g. "{{fig: A, B, C, D -
// (unlabelled diagram)}}") into the user's view.
//
// Shared across PreviewPage / ComposerPage / QuestionsView so future
// renderers automatically get the same behaviour.

export const FIG_PLACEHOLDER_RE = /\{\{\s*fig\s*:\s*[^}]+?\s*\}\}/gi;

export function stripFigPlaceholders(text: string | null | undefined): string {
  if (!text) return '';
  return text
    .replace(FIG_PLACEHOLDER_RE, '')
    // Collapse any whitespace runs created by removing a mid-line placeholder
    .replace(/[ \t]+\n/g, '\n')
    // Collapse double trailing newlines that may have been left behind
    .replace(/\n{3,}/g, '\n\n')
    .trimEnd();
}
