import { Upload } from 'lucide-react';
import { useRef, useState, type DragEvent, type KeyboardEvent } from 'react';
import { isPdfFile } from '../libraryApi';
import { Button } from '../ui/button';
import { cn } from '../ui/utils';

/** Props of {@link LibraryDropzone}. */
export interface LibraryDropzoneProps {
  /** Called with the PDF files dropped or browsed (non-PDFs are filtered out first). */
  onPdfFiles: (files: File[]) => void;
  /** Called with the names of files rejected client-side because they are not PDFs. */
  onRejected: (names: string[]) => void;
  /** Disables drop, keyboard and browse (e.g. folder not mounted). */
  disabled?: boolean;
  /** True while an upload is in flight; announced via `aria-busy`. */
  busy?: boolean;
}

/**
 * Keyboard-accessible native drag-and-drop zone plus a Browse button. Enter or Space on the zone
 * opens the file picker. Non-PDF files are rejected client-side via `onRejected`.
 */
export function LibraryDropzone({ onPdfFiles, onRejected, disabled = false, busy = false }: LibraryDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const handleFiles = (list: FileList | null) => {
    const files = Array.from(list ?? []);
    if (files.length === 0) return;
    const pdfs = files.filter(isPdfFile);
    const rejected = files.filter(file => !isPdfFile(file)).map(file => file.name);
    if (rejected.length > 0) onRejected(rejected);
    if (pdfs.length > 0) onPdfFiles(pdfs);
  };

  const open = () => {
    if (!disabled) inputRef.current?.click();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      open();
    }
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    if (!disabled) handleFiles(event.dataTransfer.files);
  };

  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-label="Upload PDF papers: drop files here or press Enter to browse"
      aria-disabled={disabled}
      aria-busy={busy}
      data-dragging={dragging}
      onClick={event => {
        if (event.target === event.currentTarget) open();
      }}
      onKeyDown={onKeyDown}
      onDragOver={event => {
        event.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      className={cn(
        'flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-border p-6 text-center text-sm text-muted-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50',
        dragging && 'border-primary bg-accent text-accent-foreground',
        disabled && 'cursor-not-allowed opacity-50',
      )}
    >
      <Upload aria-hidden="true" size={24} />
      <p>Drop PDF files here</p>
      <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={open}>
        Browse
      </Button>
      <input
        ref={inputRef}
        type="file"
        accept="application/pdf,.pdf"
        multiple
        hidden
        data-testid="library-file-input"
        aria-label="Choose PDF files"
        disabled={disabled}
        onChange={event => {
          handleFiles(event.target.files);
          event.target.value = '';
        }}
      />
    </div>
  );
}
