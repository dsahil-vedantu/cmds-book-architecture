// Frontend mirror of backend's docx_export._question_sort_key /
// _sort_question_runs. Applied at the render layer in Preview, Composer,
// and RegenReview so the user sees questions in textbook-original numeric
// order (1, 2, 3, ..., 5, 5(a), 5(b), 5(i), 6, ..., 10) — same order the
// exported DOCX produces.
//
// We sort on the frontend (not in the API responses) per user constraint:
// "i don't want to touch any api it might break few things". The underlying
// query order (by `created_at`) is unchanged.

/** Return a sortable tuple from a question_number string.
 *
 *   "1"      -> [1, "1"]
 *   "5(a)"   -> [5, "5(a)"]
 *   "5(b)"   -> [5, "5(b)"]
 *   "5(i)"   -> [5, "5(i)"]
 *   "10"     -> [10, "10"]
 *   "Q5"     -> [5, "Q5"]
 *   ""/null  -> [Infinity, ""]   missing → sort to end
 *
 * Secondary string comparison groups variants of the same parent together:
 *   5 < 5(a) < 5(b) < 5(i) < 6 < 10
 */
export function questionSortKey(
  questionNumber: string | null | undefined,
): [number, string] {
  const s = String(questionNumber ?? '').trim();
  if (!s) return [Number.POSITIVE_INFINITY, ''];
  const m = s.match(/\d+/);
  const num = m ? parseInt(m[0], 10) : Number.POSITIVE_INFINITY;
  return [num, s];
}

/** Compare two question_number strings using questionSortKey. */
export function compareQuestionNumbers(
  a: string | null | undefined,
  b: string | null | undefined,
): number {
  const [na, sa] = questionSortKey(a);
  const [nb, sb] = questionSortKey(b);
  if (na !== nb) return na - nb;
  return sa.localeCompare(sb);
}

/** Sort a plain array of question-like objects in place-equivalent
 *  (returns a new sorted array). Used by RegenReview's compare tab. */
export function sortByQuestionNumber<T extends { question_number?: string | null }>(
  list: readonly T[],
): T[] {
  return [...list].sort((a, b) =>
    compareQuestionNumbers(a.question_number, b.question_number),
  );
}

/** Mirror of backend's _sort_question_runs: walk the items list and sort
 *  each contiguous run of `type: 'question'` items by question_number.
 *  Non-question items (section_heading, block, figure, custom_text) keep
 *  their positions — only the order within a question run changes.
 *
 *  Generic over `T extends { type: string }` so this accepts the slightly-
 *  different FinalDraftItem discriminated unions defined in PreviewPage
 *  and ComposerPage (both have `type` + a `question` variant). Inside the
 *  function we narrow with a structural cast to access the optional
 *  `question.question_number` field without coupling to either caller's
 *  exact type definition. */
export function sortQuestionRuns<T extends { type: string }>(
  items: readonly T[],
): T[] {
  const getQNum = (it: T): string | null | undefined =>
    (it as unknown as { question?: { question_number?: string | null } | null })
      .question?.question_number;

  const out: T[] = [];
  let i = 0;
  while (i < items.length) {
    if (items[i].type === 'question') {
      let j = i;
      while (j < items.length && items[j].type === 'question') j++;
      const run = items.slice(i, j);
      run.sort((a, b) => compareQuestionNumbers(getQNum(a), getQNum(b)));
      out.push(...run);
      i = j;
    } else {
      out.push(items[i]);
      i++;
    }
  }
  return out;
}
