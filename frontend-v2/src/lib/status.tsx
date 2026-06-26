import type { BookStatus } from '../mocks/books';

export function StatusBadge({ status }: { status: BookStatus | string }) {
  if (status === 'done')
    return <span className="badge ok"><span className="dot" />Ready</span>;
  if (status === 'processing')
    return <span className="badge info"><span className="dot" />Processing</span>;
  if (status === 'queued')
    return <span className="badge idle"><span className="dot" />Queued</span>;
  return <span className="badge">{status}</span>;
}
