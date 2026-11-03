import { Loader2 } from 'lucide-react';
import type { LibraryPaper } from '../libraryApi';
import { Button } from '../ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../ui/table';
import { IndexStatusBadge, VerifyStatusBadge } from './LibraryStatusBadges';

/** Props of {@link LibraryPapersTable}. */
export interface LibraryPapersTableProps {
  papers: LibraryPaper[];
  /** Id of the paper currently being verified (its button shows a spinner), or null. */
  verifyingId: string | null;
  /** Disables every Verify button (e.g. a bulk job is running). */
  verifyDisabled?: boolean;
  onVerify: (paperId: string) => void;
}

/** Table of library papers: file name, title, DOI, verify and index status, per-row Verify. */
export function LibraryPapersTable({ papers, verifyingId, verifyDisabled = false, onVerify }: LibraryPapersTableProps) {
  if (papers.length === 0) {
    return (
      <p className="rounded-lg border border-border p-6 text-center text-sm text-muted-foreground">
        No papers yet. Drop PDFs above to add them.
      </p>
    );
  }
  return (
    <Table aria-label="Library papers">
      <TableHeader>
        <TableRow>
          <TableHead>File</TableHead>
          <TableHead>Title</TableHead>
          <TableHead>DOI</TableHead>
          <TableHead>Verify</TableHead>
          <TableHead>Index</TableHead>
          <TableHead className="w-24">
            <span className="sr-only">Actions</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {papers.map(paper => (
          <TableRow key={paper.id}>
            <TableCell className="max-w-48 truncate" title={paper.file_name}>
              {paper.file_name}
            </TableCell>
            <TableCell className="max-w-72 truncate" title={paper.title ?? undefined}>
              {paper.title ?? <span className="text-muted-foreground">Untitled</span>}
            </TableCell>
            <TableCell className="max-w-48 truncate" title={paper.doi ?? undefined}>
              {paper.doi ?? <span className="text-muted-foreground">No DOI</span>}
            </TableCell>
            <TableCell>
              <VerifyStatusBadge status={paper.verify_status} detail={paper.verify_detail} />
            </TableCell>
            <TableCell>
              <IndexStatusBadge status={paper.index_status} />
            </TableCell>
            <TableCell>
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-label={`Verify ${paper.file_name}`}
                disabled={verifyDisabled || verifyingId !== null}
                onClick={() => onVerify(paper.id)}
              >
                {verifyingId === paper.id ? <Loader2 aria-hidden="true" className="animate-spin" /> : null}
                Verify
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
