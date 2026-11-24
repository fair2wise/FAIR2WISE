import { Download } from 'lucide-react';
import { kgDownloadUrl, type ExportResult } from '../extractionApi';

/** `812 B`, `2.3 KB`, `2.3 MB` for a byte count. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * What an Export did, in plain words: it wrote `matkg_<name>.json` into the release folder **on the server** (that is
 * the file Publish to Tiled and "Continue existing KG" read); it did not download anything to this computer. The
 * Download link streams that file to the browser.
 */
export function ExportedNotice({ result }: { result: ExportResult | null | undefined }) {
  if (!result) return null;
  return (
    <div role="status" data-testid="export-notice" className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700">
      <p>
        Exported <strong className="font-semibold">{result.file_name}</strong>
        {typeof result.size_bytes === 'number' ? ` (${formatBytes(result.size_bytes)})` : ''}. It is stored on the
        server, in the release folder, where Publish to Tiled and Continue existing KG read it; nothing was
        downloaded to your computer.
      </p>
      <a
        href={kgDownloadUrl(result.name)}
        download={result.file_name}
        className="mt-2 inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-800 hover:bg-slate-100"
      >
        <Download aria-hidden="true" size={14} />
        Download {result.file_name}
      </a>
    </div>
  );
}
