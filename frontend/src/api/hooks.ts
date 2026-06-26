import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import {
  api,
  type BookSchema,
  type RegenParams,
  type RegenerateQuestionsParams,
  type UUID,
} from "./client";

export const qk = {
  books: () => ["books"] as const,
  book: (id: UUID) => ["books", id] as const,
  sections: (bookId: UUID) => ["books", bookId, "sections"] as const,
  section: (id: UUID) => ["sections", id] as const,
  job: (id: UUID) => ["jobs", id] as const,
  regen: (id: UUID) => ["regenerations", id] as const,
  providers: () => ["providers"] as const,
  providerKey: (name: string) => ["providers", name, "keys"] as const,
  questionBanks: (bookId: UUID) => ["books", bookId, "question-banks"] as const,
  questionBank: (id: UUID) => ["question-banks", id] as const,
  questions: (bankId: UUID) => ["question-banks", bankId, "questions"] as const,
  questionStructure: (bookId: UUID) => ["books", bookId, "question-structure"] as const,
  questionRegens: (bookId: UUID) => ["books", bookId, "question-regenerations"] as const,
  questionRegen: (id: UUID) => ["question-regenerations", id] as const,
  regenQuestions: (id: UUID) => ["question-regenerations", id, "questions"] as const,
  // Figures pipeline v2 (additive)
  bookFigures: (bookId: UUID) => ["books", bookId, "figures-v2"] as const,
  figure: (id: UUID) => ["figures-v2", id] as const,
  bookFigureRefs: (bookId: UUID, sectionRef?: string, ctx?: string) =>
    ["books", bookId, "figure-references", sectionRef ?? "all", ctx ?? "all"] as const,
  bookFigureRegens: (bookId: UUID, sectionRef?: string) =>
    ["books", bookId, "figure-regenerations", sectionRef ?? "all"] as const,
  bookUnattached: (bookId: UUID) =>
    ["books", bookId, "unattached-figures"] as const,
};

export function useBooks() {
  return useQuery({ queryKey: qk.books(), queryFn: api.listBooks });
}

export function useBook(id: UUID | null) {
  return useQuery({
    queryKey: qk.book(id ?? ""),
    queryFn: () => api.getBook(id!),
    enabled: !!id,
  });
}

export function useSections(bookId: UUID | null, opts?: { pollMs?: number }) {
  return useQuery({
    queryKey: qk.sections(bookId ?? ""),
    queryFn: () => api.listSections(bookId!),
    enabled: !!bookId,
    refetchInterval: opts?.pollMs,
    refetchIntervalInBackground: true,
  });
}

export function useSection(id: UUID | null) {
  return useQuery({
    queryKey: qk.section(id ?? ""),
    queryFn: () => api.getSection(id!),
    enabled: !!id,
  });
}

export function useJob(id: UUID | null, opts?: { pollMs?: number }) {
  const interval = opts?.pollMs ?? 1000;
  return useQuery({
    queryKey: qk.job(id ?? ""),
    queryFn: () => api.getJob(id!),
    enabled: !!id,
    refetchInterval: (query) => {
      const job = query.state.data as { status?: string } | undefined;
      if (job?.status === "succeeded" || job?.status === "failed") return false;
      return interval;
    },
    refetchIntervalInBackground: true,
  });
}

export function useRegeneration(id: UUID | null) {
  return useQuery({
    queryKey: qk.regen(id ?? ""),
    queryFn: () => api.getRegeneration(id!),
    enabled: !!id,
  });
}

export function useBookRegenerations(bookId: UUID | null) {
  return useQuery({
    queryKey: [...qk.book(bookId ?? ""), "regenerations"],
    queryFn: () => api.listRegenerations(bookId!),
    enabled: !!bookId,
  });
}

export function useProviders() {
  return useQuery({ queryKey: qk.providers(), queryFn: api.listProviders });
}

function invalidateBook(qc: QueryClient, bookId: UUID) {
  void qc.invalidateQueries({ queryKey: qk.book(bookId) });
  void qc.invalidateQueries({ queryKey: qk.sections(bookId) });
  void qc.invalidateQueries({ queryKey: qk.books() });
}

export function useUploadBook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, title }: { file: File; title?: string }) =>
      api.uploadBook(file, title),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.books() });
    },
  });
}

export function useDeleteBook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: UUID) => api.deleteBook(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.books() });
    },
  });
}

export function useAnalyse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (bookId: UUID) => api.analyse(bookId),
    onSuccess: (_data, bookId) => invalidateBook(qc, bookId),
  });
}

export function usePatchSchema() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ bookId, schema }: { bookId: UUID; schema: BookSchema }) =>
      api.patchSchema(bookId, schema),
    onSuccess: (_data, vars) => invalidateBook(qc, vars.bookId),
  });
}

export function useApprove() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (bookId: UUID) => api.approve(bookId),
    onSuccess: (_data, bookId) => invalidateBook(qc, bookId),
  });
}

export function useReExtractSection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sectionId: UUID) => api.reExtractSection(sectionId),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: qk.sections(data.book_id) });
    },
  });
}

export function useReExtractBook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (bookId: UUID) => api.reExtractBook(bookId),
    onSuccess: (_data, bookId) => invalidateBook(qc, bookId),
  });
}

export function useRegenerate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      bookId,
      params,
      sectionIds,
    }: {
      bookId: UUID;
      params: RegenParams;
      sectionIds?: string[] | null;
    }) => api.regenerate(bookId, params, sectionIds),
    onSuccess: (_data, vars) => invalidateBook(qc, vars.bookId),
  });
}

export function useRerunSection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ regenId, sectionId, customInstructions }: { regenId: UUID; sectionId: string; customInstructions: string }) =>
      api.rerunSection(regenId, sectionId, customInstructions),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.regen(vars.regenId) });
    },
  });
}

export function useSaveRegeneration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ regenId, confirmedSectionIds }: { regenId: UUID; confirmedSectionIds: string[] }) =>
      api.saveRegeneration(regenId, confirmedSectionIds),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.regen(vars.regenId) });
    },
  });
}

export function useQuestionBanks(bookId: UUID | null) {
  return useQuery({
    queryKey: qk.questionBanks(bookId ?? ""),
    queryFn: () => api.listQuestionBanks(bookId!),
    enabled: !!bookId,
  });
}

export function useQuestionBank(bankId: UUID | null, opts?: { pollMs?: number }) {
  return useQuery({
    queryKey: qk.questionBank(bankId ?? ""),
    queryFn: () => api.getQuestionBank(bankId!),
    enabled: !!bankId,
    refetchInterval: (query) => {
      const bank = query.state.data as { status?: string } | undefined;
      if (bank?.status === "ready" || bank?.status === "failed") return false;
      return opts?.pollMs ?? 2000;
    },
  });
}

export function useQuestions(
  bankId: UUID | null,
  opts?: { bankStatus?: string; pollMs?: number },
) {
  return useQuery({
    queryKey: qk.questions(bankId ?? ""),
    queryFn: () => api.listQuestions(bankId!),
    enabled: !!bankId,
    refetchInterval: () => {
      const s = opts?.bankStatus;
      if (s === "ready" || s === "failed" || !s) return false;
      return opts?.pollMs ?? 2000;
    },
  });
}

export function useQuestionStructure(
  bookId: UUID | null,
  opts?: { pollWhileExtracting?: boolean },
) {
  return useQuery({
    queryKey: qk.questionStructure(bookId ?? ""),
    queryFn: () => api.getQuestionStructure(bookId!),
    enabled: !!bookId,
    // Poll every 2s while the caller flags an active extraction so sidebar
    // folder counts tick up block-by-block.
    refetchInterval: opts?.pollWhileExtracting ? 2000 : false,
  });
}

export function useCreateQuestionBank() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (bookId: UUID) => api.createQuestionBank(bookId),
    onSuccess: (_data, bookId) => {
      void qc.invalidateQueries({ queryKey: qk.questionBanks(bookId) });
    },
  });
}

export function useRetrySection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ bankId, sectionRef }: { bankId: UUID; sectionRef: string }) =>
      api.retrySection(bankId, sectionRef),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.questionBank(vars.bankId) });
      void qc.invalidateQueries({ queryKey: qk.questions(vars.bankId) });
    },
  });
}

// R6 — per-section retry inside an existing question-regen run
export function useRetryRegenSection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ regenId, sectionRef }: { regenId: UUID; sectionRef: string }) =>
      api.retryRegenSection(regenId, sectionRef),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.regenQuestions(vars.regenId) });
      void qc.invalidateQueries({ queryKey: qk.questionRegen(vars.regenId) });
    },
  });
}

export function useReExtractBlock() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ bankId, blockIdx }: { bankId: UUID; blockIdx: number }) =>
      api.reExtractBlock(bankId, blockIdx),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.questionBank(vars.bankId) });
      void qc.invalidateQueries({ queryKey: qk.questions(vars.bankId) });
    },
  });
}

export function useRestoreRejected() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ bankId, rejectedId }: { bankId: UUID; rejectedId: UUID }) =>
      api.restoreRejected(bankId, rejectedId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.questions(vars.bankId) });
      void qc.invalidateQueries({ queryKey: qk.questionBank(vars.bankId) });
    },
  });
}

export function useRestoreAllRejected() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ bankId }: { bankId: UUID }) =>
      api.restoreAllRejected(bankId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.questions(vars.bankId) });
      void qc.invalidateQueries({ queryKey: qk.questionBank(vars.bankId) });
    },
  });
}

export function useDiscardRejected() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ bankId, rejectedId }: { bankId: UUID; rejectedId: UUID }) =>
      api.discardRejected(bankId, rejectedId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.questions(vars.bankId) });
    },
  });
}

export function useHideQuestion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ questionId }: { bankId: UUID; questionId: UUID }) =>
      api.hideQuestion(questionId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.questions(vars.bankId) });
    },
  });
}

export function useUnhideQuestion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ questionId }: { bankId: UUID; questionId: UUID }) =>
      api.unhideQuestion(questionId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.questions(vars.bankId) });
    },
  });
}

export function useDeleteQuestionBank() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ bankId }: { bankId: UUID; bookId: UUID }) =>
      api.deleteQuestionBank(bankId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.questionBanks(vars.bookId) });
      void qc.invalidateQueries({ queryKey: qk.questionBank(vars.bankId) });
    },
  });
}

export function useQuestionRegenerations(bookId: UUID | null, opts?: { pollMs?: number }) {
  return useQuery({
    queryKey: qk.questionRegens(bookId ?? ""),
    queryFn: () => api.listQuestionRegenerations(bookId!),
    enabled: !!bookId,
    refetchInterval: opts?.pollMs,
  });
}

export function useQuestionRegen(regenId: UUID | null, opts?: { pollMs?: number }) {
  return useQuery({
    queryKey: qk.questionRegen(regenId ?? ""),
    queryFn: () => api.getQuestionRegeneration(regenId!),
    enabled: !!regenId,
    refetchInterval: (query) => {
      const r = query.state.data as { status?: string } | undefined;
      if (r?.status === "ready" || r?.status === "failed" || r?.status === "saved") return false;
      return opts?.pollMs ?? 2000;
    },
  });
}

export function useRegenQuestions(regenId: UUID | null, opts?: { pollMs?: number }) {
  return useQuery({
    queryKey: qk.regenQuestions(regenId ?? ""),
    queryFn: () => api.listRegenQuestions(regenId!),
    enabled: !!regenId,
    refetchInterval: opts?.pollMs,
  });
}

export function useStartQuestionRegeneration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ bankId, params }: { bankId: UUID; params: RegenerateQuestionsParams }) =>
      api.startQuestionRegeneration(bankId, params),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.questionBank(vars.bankId) });
      void qc.invalidateQueries({ queryKey: ["books"] });
    },
  });
}

export function useDeleteQuestionRegeneration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ regenId }: { regenId: UUID; bookId: UUID }) =>
      api.deleteQuestionRegeneration(regenId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.questionRegens(vars.bookId) });
      void qc.invalidateQueries({ queryKey: qk.questionRegen(vars.regenId) });
    },
  });
}

export function useSaveQuestionRegeneration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ regenId }: { regenId: UUID; bookId: UUID }) =>
      api.saveQuestionRegeneration(regenId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.questionRegens(vars.bookId) });
      void qc.invalidateQueries({ queryKey: qk.questionRegen(vars.regenId) });
    },
  });
}

export function useBulkDeleteRegenQuestions() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ regenId, questionIds }: { regenId: UUID; questionIds: UUID[] }) =>
      api.bulkDeleteRegenQuestions(regenId, questionIds),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.regenQuestions(vars.regenId) });
      void qc.invalidateQueries({ queryKey: qk.questionRegen(vars.regenId) });
    },
  });
}

export function useSaveProviderKeys() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, keys }: { name: string; keys: Record<string, unknown> }) =>
      api.saveProviderKeys(name, keys),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.providers() });
    },
  });
}

// ============================================================
// Figures pipeline v2 hooks (NEW — additive)
// ============================================================

import type { FigureRegenParams } from "./client";

export function useBookFigures(bookId: UUID | null, opts?: { pollMs?: number }) {
  return useQuery({
    queryKey: qk.bookFigures(bookId ?? ""),
    queryFn: () => api.listFigures(bookId!),
    enabled: !!bookId,
    refetchInterval: opts?.pollMs,
  });
}

export function useFigure(figureId: UUID | null, opts?: { pollMs?: number }) {
  return useQuery({
    queryKey: qk.figure(figureId ?? ""),
    queryFn: () => api.getFigure(figureId!),
    enabled: !!figureId,
    refetchInterval: opts?.pollMs,
  });
}

export function useBookFigureRefs(
  bookId: UUID | null,
  opts?: { sectionRef?: string; context?: "theory" | "question" },
) {
  return useQuery({
    queryKey: qk.bookFigureRefs(bookId ?? "", opts?.sectionRef, opts?.context),
    queryFn: () => api.listFigureReferences(bookId!, opts),
    enabled: !!bookId,
  });
}

export function useBookUnattachedFigures(bookId: UUID | null) {
  return useQuery({
    queryKey: qk.bookUnattached(bookId ?? ""),
    queryFn: () => api.listUnattachedFigures(bookId!),
    enabled: !!bookId,
  });
}

export function useHideFigureReference() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ refId }: { refId: UUID; bookId: UUID }) =>
      api.hideFigureReference(refId),
    onSuccess: (_data, vars) => {
      // Invalidate all section + question views so the figure disappears
      void qc.invalidateQueries({ queryKey: qk.sections(vars.bookId) });
      void qc.invalidateQueries({ queryKey: ["question-banks"] });
      void qc.invalidateQueries({ queryKey: qk.bookUnattached(vars.bookId) });
    },
  });
}

export function useUnhideFigureReference() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ refId }: { refId: UUID; bookId: UUID }) =>
      api.unhideFigureReference(refId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.sections(vars.bookId) });
      void qc.invalidateQueries({ queryKey: ["question-banks"] });
      void qc.invalidateQueries({ queryKey: qk.bookUnattached(vars.bookId) });
    },
  });
}

export function useDeleteFigureReference() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ refId }: { refId: UUID; bookId: UUID }) =>
      api.deleteFigureReference(refId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.sections(vars.bookId) });
      void qc.invalidateQueries({ queryKey: ["question-banks"] });
      void qc.invalidateQueries({ queryKey: qk.bookUnattached(vars.bookId) });
    },
  });
}

/* ─── Final Draft (composer) ─────────────────────────────────────────── */

export function useFinalDraft(
  bookId: UUID | null,
  preferRegen: boolean = true,
) {
  return useQuery({
    queryKey: ["books", bookId, "final-draft"] as const,
    queryFn: () => api.getFinalDraft(bookId!, preferRegen),
    enabled: !!bookId,
    // Always pull fresh on page mount so Preview reflects the latest
    // edits saved in Composer (no stale-cache lag between tabs).
    refetchOnMount: "always",
    refetchOnWindowFocus: true,
    staleTime: 0,
  });
}

export function usePatchFinalDraft(bookId: UUID | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (operations: Parameters<typeof api.patchFinalDraft>[1]) =>
      api.patchFinalDraft(bookId!, operations),
    onSuccess: (data) => {
      // Three-step cache propagation:
      //   1. setQueryData on the final-draft key → instant for any consumer
      //      (Composer, Preview, and Final tab's draft branch).
      //   2. invalidateQueries on the same key → forces refetch on any
      //      stale mounted consumer.
      //   3. invalidateQueries on the merge key → Final tab also reads
      //      final-merge as a fallback when the draft is empty; bust that
      //      cache too so it never serves stale data after a draft edit.
      qc.setQueryData(["books", bookId, "final-draft"], data);
      void qc.invalidateQueries({
        queryKey: ["books", bookId, "final-draft"],
      });
      void qc.invalidateQueries({
        queryKey: ["books", bookId, "final-merge"],
      });
    },
  });
}

export function useReseedFinalDraft(bookId: UUID | null) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (preferRegen: boolean = true) =>
      api.reseedFinalDraft(bookId!, preferRegen),
    onSuccess: (data) => {
      qc.setQueryData(["books", bookId, "final-draft"], data);
    },
  });
}

export function useFinalMerge(
  bookId: UUID | null,
  preferRegen: boolean = true,
) {
  return useQuery({
    queryKey: ["books", bookId, "final-merge", preferRegen] as const,
    queryFn: () => api.getFinalMerge(bookId!, preferRegen),
    enabled: !!bookId,
  });
}

export function useReembedFigures() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (bookId: UUID) => api.reembedFigures(bookId),
    onSuccess: (_data, bookId) => {
      void qc.invalidateQueries({ queryKey: qk.sections(bookId) });
      void qc.invalidateQueries({ queryKey: ["question-banks"] });
      void qc.invalidateQueries({ queryKey: qk.bookUnattached(bookId) });
      void qc.invalidateQueries({ queryKey: qk.bookFigures(bookId) });
    },
  });
}

export function useBookFigureRegenerations(
  bookId: UUID | null,
  opts?: { sectionRef?: string; pollMs?: number },
) {
  return useQuery({
    queryKey: qk.bookFigureRegens(bookId ?? "", opts?.sectionRef),
    queryFn: () => api.listFigureRegenerations(bookId!, { sectionRef: opts?.sectionRef }),
    enabled: !!bookId,
    refetchInterval: opts?.pollMs,
  });
}

export function useExtractFiguresV2() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ bookId }: { bookId: UUID }) => api.extractFiguresV2(bookId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.bookFigures(vars.bookId) });
    },
  });
}

export function useRegenerateFiguresSection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      bookId,
      sectionRef,
      params,
    }: {
      bookId: UUID;
      sectionRef: string;
      params: FigureRegenParams;
    }) => api.regenerateFiguresSection(bookId, sectionRef, params),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.bookFigures(vars.bookId) });
      void qc.invalidateQueries({ queryKey: qk.bookFigureRegens(vars.bookId) });
    },
  });
}

export function useDiscardFigureRegen() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ figureId }: { figureId: UUID; bookId: UUID }) =>
      api.discardFigureRegen(figureId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.figure(vars.figureId) });
      void qc.invalidateQueries({ queryKey: qk.bookFigures(vars.bookId) });
    },
  });
}

// 0016 — approval workflow (Q5 "Approve & move to Regenerated")
export function useApproveSectionFigures() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ bookId, sectionRef }: { bookId: UUID; sectionRef: string }) =>
      api.approveSectionFigures(bookId, sectionRef),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.bookFigures(vars.bookId) });
    },
  });
}

export function useUnapproveSectionFigures() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ bookId, sectionRef }: { bookId: UUID; sectionRef: string }) =>
      api.unapproveSectionFigures(bookId, sectionRef),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.bookFigures(vars.bookId) });
    },
  });
}

export function useApproveFigure() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ figureId }: { figureId: UUID; bookId: UUID }) =>
      api.approveOneFigure(figureId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.figure(vars.figureId) });
      void qc.invalidateQueries({ queryKey: qk.bookFigures(vars.bookId) });
    },
  });
}

export function useUnapproveFigure() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ figureId }: { figureId: UUID; bookId: UUID }) =>
      api.unapproveOneFigure(figureId),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.figure(vars.figureId) });
      void qc.invalidateQueries({ queryKey: qk.bookFigures(vars.bookId) });
    },
  });
}
