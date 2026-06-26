import { create } from "zustand";
import type { UUID } from "../api/client";

export type View = "library" | "upload" | "schema" | "reader" | "regen" | "settings" | "questions" | "images" | "final" | "compose" | "preview";
export type BookLens = "theory" | "questions" | "images";

interface UIState {
  view: View;
  selectedBookId: UUID | null;
  selectedSectionId: UUID | null;
  activeJobId: UUID | null;
  selectedRegenId: UUID | null;
  selectedBankId: UUID | null;
  selectedQuestionRegenId: UUID | null;
  // Questions view — schema section id (string, e.g. "8.4") or excluded_block_ref
  selectedQuestionSectionRef: string | null;
  selectedExcludedBlockRef: string | null;
  // Sidebar lens toggle (per selected book). Theory = flat sections. Questions = per-kind folders.
  bookLens: BookLens;
  // Selected kind folder inside Questions lens (e.g. "example", "exercise")
  selectedKind: string | null;
  // Figures pipeline (additive — no overlap with Q/T state)
  selectedFigureId: UUID | null;
  selectedFigureSectionRef: string | null;
  setView: (v: View) => void;
  selectBook: (id: UUID | null) => void;
  selectSection: (id: UUID | null) => void;
  setJob: (id: UUID | null) => void;
  setRegenId: (id: UUID | null) => void;
  selectBank: (id: UUID | null) => void;
  selectQuestionRegen: (id: UUID | null) => void;
  selectQuestionSection: (sectionRef: string | null) => void;
  selectExcludedBlock: (ref: string | null, sectionRef?: string | null) => void;
  setBookLens: (lens: BookLens) => void;
  selectKind: (sectionRef: string | null, kind: string | null) => void;
  selectFigure: (figureId: UUID | null) => void;
  selectFigureSection: (sectionRef: string | null) => void;
}

export const useUI = create<UIState>((set) => ({
  view: "library",
  selectedBookId: null,
  selectedSectionId: null,
  activeJobId: null,
  selectedRegenId: null,
  selectedBankId: null,
  selectedQuestionRegenId: null,
  selectedQuestionSectionRef: null,
  selectedExcludedBlockRef: null,
  bookLens: "theory",
  selectedKind: null,
  selectedFigureId: null,
  selectedFigureSectionRef: null,
  setView: (view) => set({ view }),
  selectBook: (id) =>
    set({
      selectedBookId: id,
      selectedSectionId: null,
      selectedRegenId: null,
      selectedBankId: null,
      selectedQuestionRegenId: null,
      selectedQuestionSectionRef: null,
      selectedExcludedBlockRef: null,
      bookLens: "theory",
      selectedKind: null,
    }),
  selectSection: (id) => set({ selectedSectionId: id }),
  setJob: (id) => set({ activeJobId: id }),
  setRegenId: (id) => set({ selectedRegenId: id }),
  selectBank: (id) => set({ selectedBankId: id, selectedSectionId: null, selectedQuestionRegenId: null }),
  selectQuestionRegen: (id) => set({ selectedQuestionRegenId: id }),
  selectQuestionSection: (sectionRef) =>
    set({ selectedQuestionSectionRef: sectionRef, selectedExcludedBlockRef: null }),
  selectExcludedBlock: (ref, sectionRef = null) =>
    set({
      selectedExcludedBlockRef: ref,
      selectedQuestionSectionRef: sectionRef,
      selectedKind: null,
    }),
  setBookLens: (lens) =>
    set({
      bookLens: lens,
      // Moving to Questions mode clears reader selection; moving back clears question scope.
      ...(lens === "theory"
        ? { selectedQuestionSectionRef: null, selectedExcludedBlockRef: null, selectedKind: null }
        : { selectedSectionId: null, selectedRegenId: null }),
    }),
  selectKind: (sectionRef, kind) =>
    set({
      selectedQuestionSectionRef: sectionRef,
      selectedKind: kind,
      selectedExcludedBlockRef: null,
    }),
  selectFigure: (figureId) => set({ selectedFigureId: figureId }),
  selectFigureSection: (sectionRef) =>
    set({ selectedFigureSectionRef: sectionRef, selectedFigureId: null }),
}));
