// Questions regen params card. Mirrors RegenerateRequest in
// backend/app/api/question_regenerations.py.

import {
  ParamLabel,
  ParamRow,
  ParamTextarea,
  SegmentChoice,
} from './PipelineCard';
import type {
  QuestionsRegenParams,
  QuestionsSimilarity,
} from '../../api/regen';

type Props = {
  value: QuestionsRegenParams;
  onChange: (next: QuestionsRegenParams) => void;
};

const SIMILARITY_OPTS: readonly QuestionsSimilarity[] = [
  'numbers_only',
  'numbers_and_rephrase',
  'numbers_rephrase_add_concept',
  'new_question_same_topic',
  'same_topic_add_one_concept',
  'same_chapter_any_topic',
] as const;

const SIMILARITY_LABEL: Record<QuestionsSimilarity, string> = {
  numbers_only: 'Numbers only',
  numbers_and_rephrase: 'Numbers + rephrase',
  numbers_rephrase_add_concept: 'Numbers + rephrase + concept',
  new_question_same_topic: 'New Q, same topic',
  same_topic_add_one_concept: 'Add one concept',
  same_chapter_any_topic: 'Any topic in chapter',
};

export function QuestionsParams({ value, onChange }: Props) {
  const upd = <K extends keyof QuestionsRegenParams>(
    k: K,
    v: QuestionsRegenParams[K],
  ) => onChange({ ...value, [k]: v });

  return (
    <div>
      <ParamRow>
        <ParamLabel hint="How different the regenerated questions should be from the originals.">
          Similarity level
        </ParamLabel>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))',
            gap: 6,
          }}
        >
          {SIMILARITY_OPTS.map((opt) => {
            const active = value.similarity_level === opt;
            return (
              <button
                key={opt}
                type="button"
                onClick={() => upd('similarity_level', opt)}
                style={{
                  padding: '8px 12px',
                  border: active ? '1.5px solid var(--indigo-700)' : '1px solid var(--line)',
                  borderRadius: 8,
                  background: active ? 'var(--indigo-50)' : 'var(--surface)',
                  color: active ? 'var(--indigo-700)' : 'var(--ink-800)',
                  fontSize: 12,
                  fontWeight: active ? 700 : 500,
                  cursor: 'pointer',
                  textAlign: 'left',
                }}
              >
                {SIMILARITY_LABEL[opt]}
              </button>
            );
          })}
        </div>
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Optional — restrict to one question type (MCQ, Short answer, etc.).">
          Question type
        </ParamLabel>
        <input
          type="text"
          value={value.question_type ?? ''}
          onChange={(e) => upd('question_type', e.target.value || null)}
          placeholder="e.g. MCQ"
          style={{
            width: '100%',
            height: 38,
            padding: '0 12px',
            border: '1px solid var(--line)',
            borderRadius: 10,
            font: 'inherit',
            fontSize: 13,
          }}
        />
      </ParamRow>

      <ParamRow>
        <ParamLabel hint="Free-form guidance for the question regenerator.">
          Custom instructions
        </ParamLabel>
        <ParamTextarea
          value={value.custom_instructions ?? ''}
          onChange={(v) => upd('custom_instructions', v || null)}
          placeholder="e.g. Keep difficulty Class-10 level. No assertion-reason."
        />
      </ParamRow>
    </div>
  );
}
