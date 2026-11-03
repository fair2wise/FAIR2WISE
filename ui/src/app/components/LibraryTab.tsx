import { Library, Loader2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import { TERMINAL_JOB_STATES } from './extractionApi';
import { useJob } from './extractionHooks';
import { paperFolders, uploadErrorMessage } from './libraryApi';
import {
  libraryKeys,
  useIndexLibrary,
  useLibraryPapers,
  useLibraryProbe,
  useUploadPaper,
  useVerifyAllPapers,
  useVerifyPaper,
} from './libraryHooks';
import { useQueryClient } from '@tanstack/react-query';
import { FolderFilter } from './library/FolderFilter';
import { NEW_CATEGORY, resolveUploadFolder, UploadTarget, type UploadTargetValue } from './library/UploadTarget';
import { LibraryDropzone } from './library/LibraryDropzone';
import { LibraryPapersTable } from './library/LibraryPapersTable';
import { HarvesterPanel } from './library/harvest/HarvesterPanel';
import { Button } from './ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';

const JOB_POLL_MS = 2000;
const LIST_POLL_MS = 3000;

/** A bulk job this tab submitted and is following. */
interface TrackedJob {
  id: string;
  kind: 'library_verify' | 'library_index';
}

const newNonce = () => (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`);

/**
 * Library tab (A8, T41): the papers table, PDF upload (drag-and-drop or Browse), per-row Verify,
 * bulk "Verify all" and "Index for QA" jobs. While a job runs the table is refetched; on completion
 * the library queries are invalidated and a toast reports the outcome. Shows an empty state and a
 * "folder not mounted" state (status `mounted: false`, or an upload answering 501).
 */
export function LibraryTab() {
  const queryClient = useQueryClient();
  const probe = useLibraryProbe();
  const status = probe.status;
  const [notMounted501, setNotMounted501] = useState(false);
  const mounted = (status?.mounted ?? false) && !notMounted501;

  const [panel, setPanel] = useState<'papers' | 'harvester'>('papers');
  const [tracked, setTracked] = useState<TrackedJob | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [target, setTarget] = useState<UploadTargetValue>({ choice: '', newName: '' });
  const uploadFolder = resolveUploadFolder(target);

  const job = useJob(tracked?.id ?? null, JOB_POLL_MS);
  const jobRunning = tracked !== null && !(job.data && TERMINAL_JOB_STATES.has(job.data.state));
  const papersQuery = useLibraryPapers(mounted, jobRunning ? LIST_POLL_MS : false);

  const upload = useUploadPaper();
  const verifyOne = useVerifyPaper();
  const verifyAll = useVerifyAllPapers();
  const indexAll = useIndexLibrary();

  // React once when the followed job reaches a terminal state.
  const reported = useRef<string | null>(null);
  useEffect(() => {
    if (!tracked || !job.data || !TERMINAL_JOB_STATES.has(job.data.state)) return;
    if (reported.current === tracked.id) return;
    reported.current = tracked.id;
    const label = tracked.kind === 'library_verify' ? 'Verify all' : 'Index for QA';
    if (job.data.state === 'succeeded') toast.success(`${label} finished.`);
    else toast.error(`${label} ${job.data.state}${job.data.error ? `: ${job.data.error}` : ''}`);
    void queryClient.invalidateQueries({ queryKey: libraryKeys.all });
    setTracked(null);
  }, [tracked, job.data, queryClient]);

  const submitJob = (kind: TrackedJob['kind'], mutation: typeof verifyAll | typeof indexAll) => {
    mutation.mutate(newNonce(), {
      onSuccess: ref => setTracked({ id: ref.id, kind }),
      onError: error => toast.error(error.message),
    });
  };

  const uploadFiles = async (files: File[]) => {
    setUploadError(null);
    setUploading(true);
    const errors: string[] = [];
    for (const file of files) {
      try {
        const result = await upload.mutateAsync({ file, folder: uploadFolder || undefined });
        if (result.duplicate) toast.info(`${file.name} is already in the library (${result.paper.relpath}).`);
        else toast.success(`Uploaded ${file.name} to ${result.paper.relpath}.`);
        if (result.manifest && !result.manifest.updated && result.manifest.detail !== 'already listed in the manifest') {
          toast.warning(result.manifest.detail);
        }
      } catch (error) {
        if ((error as { status?: number }).status === 501) setNotMounted501(true);
        const message = `${file.name}: ${uploadErrorMessage(error)}`;
        errors.push(message);
        toast.error(message);
      }
    }
    setUploading(false);
    // A category created by this upload now exists: keep it selected as an existing one.
    if (target.choice === NEW_CATEGORY && uploadFolder && errors.length < files.length) {
      setTarget({ choice: uploadFolder, newName: '' });
    }
    if (errors.length > 0) setUploadError(errors.join(' '));
  };

  const allPapers = papersQuery.data?.papers ?? [];
  const folders = useMemo(() => paperFolders(allPapers), [allPapers]);
  const [folder, setFolder] = useState<string | null>(null);
  // A folder that disappeared (files removed) falls back to "All" instead of an empty table.
  const activeFolder = folder !== null && folders.some(entry => entry.name === folder) ? folder : null;
  const papers = activeFolder === null ? allPapers : allPapers.filter(paper => paper.folder === activeFolder);
  const busyJob = jobRunning || verifyAll.isPending || indexAll.isPending;
  const actionsDisabled = !mounted || busyJob;
  const indexedCount = status?.indexed_count ?? null;

  return (
    <section
      aria-labelledby="library-tab-title"
      className="flex h-full w-full flex-col gap-4 overflow-auto rounded-xl border border-border bg-card p-6 text-card-foreground"
    >
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Library aria-hidden="true" size={24} />
          <h2 id="library-tab-title" className="text-lg font-semibold">
            Library
          </h2>
          {mounted && indexedCount !== null ? (
            <span className="text-sm text-muted-foreground">{indexedCount} indexed</span>
          ) : null}
        </div>
        {panel === 'papers' ? (
        <div className="flex items-center gap-2">
          {jobRunning ? (
            <span role="status" className="flex items-center gap-1 text-sm text-muted-foreground">
              <Loader2 aria-hidden="true" className="size-4 animate-spin" />
              {tracked?.kind === 'library_verify' ? 'Verifying papers…' : 'Indexing papers…'}
            </span>
          ) : null}
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={actionsDisabled || allPapers.length === 0}
            onClick={() => submitJob('library_verify', verifyAll)}
          >
            Verify all
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={actionsDisabled || allPapers.length === 0}
            onClick={() => submitJob('library_index', indexAll)}
          >
            Index for QA
          </Button>
        </div>
        ) : null}
      </header>

      <Tabs value={panel} onValueChange={value => setPanel(value as 'papers' | 'harvester')}>
        <TabsList aria-label="Library sections" className="self-start">
          <TabsTrigger value="papers" className="px-4">Papers</TabsTrigger>
          <TabsTrigger value="harvester" className="px-4" disabled={!mounted}>Harvester</TabsTrigger>
        </TabsList>
        <TabsContent value="papers" className="flex flex-col gap-4 pt-2">
      {!mounted ? (
        <p role="status" className="rounded-lg border border-border p-6 text-center text-sm text-muted-foreground">
          The papers folder is not mounted on this server, so the library is read-only here.
        </p>
      ) : null}

      <UploadTarget folders={folders} value={target} onChange={setTarget} disabled={!mounted} />
      <LibraryDropzone
        disabled={!mounted || uploadFolder === null}
        busy={uploading}
        onPdfFiles={files => void uploadFiles(files)}
        onRejected={names => {
          const message = `Only PDF files can be uploaded. Rejected: ${names.join(', ')}`;
          setUploadError(message);
          toast.error(message);
        }}
      />
      {uploadError ? (
        <p role="alert" className="text-sm text-destructive">
          {uploadError}
        </p>
      ) : null}

      {status?.quota_open ? (
        <p role="status" className="text-sm text-muted-foreground">
          The registry quota is exhausted; Verify may report &quot;Quota reached&quot; until it resets.
        </p>
      ) : null}
      {papersQuery.isError ? (
        <p role="alert" className="text-sm text-destructive">
          {papersQuery.error.message}
        </p>
      ) : null}
      {papersQuery.data?.truncated ? (
        <p className="text-sm text-muted-foreground">
          Showing {papers.length} of {papersQuery.data.total} papers.
        </p>
      ) : null}
      {(papersQuery.data?.pending_metadata ?? 0) > 0 ? (
        <p role="status" className="text-sm text-muted-foreground">
          Reading titles and DOIs for {papersQuery.data?.pending_metadata} paper(s)…
        </p>
      ) : null}

      {mounted && papersQuery.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading papers…
        </p>
      ) : null}
      {mounted && folders.length > 1 ? (
        <FolderFilter folders={folders} total={allPapers.length} value={activeFolder} onChange={setFolder} />
      ) : null}
      {mounted && papersQuery.data ? (
        <LibraryPapersTable
          papers={papers}
          verifyingId={verifyOne.isPending ? verifyOne.variables : null}
          verifyDisabled={jobRunning}
          onVerify={id =>
            verifyOne.mutate(id, {
              onSuccess: result => {
                if (result.paper.verify_status === 'quota_open') {
                  toast.warning('Registry quota reached; try Verify again later.');
                }
              },
              onError: error => toast.error(error.message),
            })
          }
        />
      ) : null}
        </TabsContent>
        <TabsContent value="harvester" className="pt-2">
          <HarvesterPanel />
        </TabsContent>
      </Tabs>
    </section>
  );
}
