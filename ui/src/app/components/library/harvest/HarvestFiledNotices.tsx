import { CheckCircle2, Loader2, X } from 'lucide-react';
import type { MatchKind } from '../../harvestApi';
import { Button } from '../../ui/button';

/** A filing the user just made from a supplied PDF, kept visible with an Undo until dismissed. */
export interface FiledNotice {
  /** The candidate the PDF was filed against (what Undo reverses). */
  candidateId: string;
  fileName: string;
  title: string;
  /** `strong` filed at once; `weak` and `mismatch` were confirmed by the user. */
  kind: MatchKind;
}

/** Props of {@link HarvestFiledNotices}. */
export interface HarvestFiledNoticesProps {
  notices: FiledNotice[];
  /** Candidate id whose undo is in flight. */
  undoingId: string | null;
  onUndo: (notice: FiledNotice) => void;
  onClose: (notice: FiledNotice) => void;
}

/** How the match is worded after the fact. */
export function noticeHow(kind: MatchKind): string {
  return kind === 'strong' ? 'strong match' : kind === 'weak' ? 'confirmed by you' : 'mismatch confirmed by you';
}

/** "Filed <file> against <title> (strong match)" for each recent filing, with Undo. */
export function HarvestFiledNotices({ notices, undoingId, onUndo, onClose }: HarvestFiledNoticesProps) {
  if (notices.length === 0) return null;
  return (
    <ul aria-label="Recently filed PDFs" className="space-y-2">
      {notices.map(notice => (
        <li
          key={`${notice.candidateId}:${notice.fileName}`}
          role="status"
          className="flex flex-wrap items-center gap-2 rounded-md border border-emerald-300 bg-emerald-50 p-2 text-sm text-emerald-900"
        >
          <CheckCircle2 aria-hidden="true" className="size-4 shrink-0" />
          <span className="min-w-0 flex-1 break-words">
            Filed <span className="font-medium">{notice.fileName}</span> against{' '}
            <span className="font-medium">{notice.title}</span> ({noticeHow(notice.kind)})
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={undoingId === notice.candidateId}
            aria-label={`Undo filing of ${notice.fileName}`}
            onClick={() => onUndo(notice)}
            className="disabled:cursor-not-allowed disabled:opacity-50"
          >
            {undoingId === notice.candidateId ? <Loader2 aria-hidden="true" className="animate-spin" /> : null}
            Undo
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={`Hide the note about ${notice.fileName}`}
            onClick={() => onClose(notice)}
            className="size-8"
          >
            <X aria-hidden="true" />
          </Button>
        </li>
      ))}
    </ul>
  );
}
