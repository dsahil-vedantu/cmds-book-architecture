// Folder API client + hooks.
//
// Folders are the V-Studio "book folder" entity: each one groups multiple
// uploaded chapter PDFs. The backend computes the aggregate counts
// (chapters / questions / figures + status breakdown) so the library page
// doesn't have to fan-out per folder.

import { useQuery } from '@tanstack/react-query';

import { ApiError, req } from './client';

export type Folder = {
  id: string;
  name: string;
  color: string;
  subject: string | null;
  created_at: string;
  updated_at: string;
  chapters: number;
  questions: number;
  figures: number;
  chapters_ready: number;
  chapters_processing: number;
  chapters_queued: number;
};

export type FolderCreate = {
  name: string;
  subject?: string;
  color?: string;
};

// ---------- HTTP ----------

export const listFolders = () => req<Folder[]>('/api/folders');

export const getFolder = (id: string) => req<Folder>(`/api/folders/${id}`);

export const createFolder = (body: FolderCreate) =>
  req<Folder>('/api/folders', { method: 'POST', body: JSON.stringify(body) });

export const deleteFolder = (id: string) =>
  req<void>(`/api/folders/${id}`, { method: 'DELETE' });

// ---------- Hooks ----------

type ListState =
  | { kind: 'loading' }
  | { kind: 'ready'; folders: Folder[] }
  | { kind: 'error'; error: string };

export function useFolders() {
  const q = useQuery({
    queryKey: ['folders'],
    queryFn: listFolders,
  });

  const state: ListState = q.isPending
    ? { kind: 'loading' }
    : q.error
      ? { kind: 'error', error: explain(q.error) }
      : { kind: 'ready', folders: q.data! };

  return { ...state, refetch: () => q.refetch() };
}

type OneState =
  | { kind: 'loading' }
  | { kind: 'ready'; folder: Folder }
  | { kind: 'error'; error: string };

export function useFolder(id: string | undefined) {
  const q = useQuery({
    queryKey: ['folder', id],
    queryFn: () => getFolder(id!),
    enabled: Boolean(id),
  });

  const state: OneState = !id
    ? { kind: 'error', error: 'No folder id in URL' }
    : q.isPending
      ? { kind: 'loading' }
      : q.error
        ? { kind: 'error', error: explain(q.error) }
        : { kind: 'ready', folder: q.data! };

  return { ...state, refetch: () => q.refetch() };
}

function explain(err: unknown): string {
  if (err instanceof ApiError) return `Backend ${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return 'Unknown error';
}
