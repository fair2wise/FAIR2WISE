import { useQueryClient } from '@tanstack/react-query';
import { Loader2, Play } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { toast } from 'sonner';
import {
  harvestFormProblem,
  HARVEST_SOURCES,
  MAX_DOWNLOADS_DEFAULT,
  MAX_DOWNLOADS_MAX,
  MAX_DOWNLOADS_MIN,
  TOPIC_MAX_CHARS,
  type HarvestCandidate,
  type HarvestSource,
} from '../../harvestApi';
import {
  harvestKeys,
  useDismissCandidate,
  useFileHarvestPdf,
  useHarvestCandidates,
  useHarvestRuns,
  useRunningHarvest,
  useStartHarvest,
} from '../../harvestHooks';
import { TERMINAL_JOB_STATES } from '../../extractionApi';
import { FAST_POLL_MS, useCancelJob, useJob, useSchemas } from '../../extractionHooks';
import { useJobLogPolling } from '../../extraction/useJobLogPolling';
import { isValidFolderName } from '../../libraryApi';
import { libraryKeys, useCreateCategory, useLibraryCategories } from '../../libraryHooks';
import { useLive } from '../../tabActivity';
import { Button } from '../../ui/button';
import { Checkbox } from '../../ui/checkbox';
import { Input } from '../../ui/input';
import { Label } from '../../ui/label';
import { NewCategoryField } from '../NewCategoryField';
import { RunLog } from '../../extraction/RunLog';
import { HarvestResults } from './HarvestResults';
import { HarvestRunsList } from './HarvestRunsList';
import { HarvestRunView } from './HarvestRunView';

/** `value` of the "new category" option of the category select. */
const NEW_CATEGORY = '\u0000new';

const SELECT_CLASS =
  'h-9 w-full min-w-0 rounded-md border border-border bg-background px-3 text-sm shadow-sm outline-none '
  + 'focus:border-ring disabled:cursor-not-allowed disabled:opacity-50';

const POLL_MS = 3000;

const newNonce = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

/**
 * Harvester panel of the Library tab (A8, T46): search arXiv and OpenAlex for a topic and file the
 * open-access PDFs into a library category; paywalled hits wait in a queue for a person to supply the PDF.
 *
 * The form picks the topic, the target category (existing folder or a new one), a schema (a label recorded
 * with the run; it does not change the search), the sources and the download cap. While a `library_harvest`
 * job runs the run view (progress, live log, Cancel) replaces the form; a run in progress is re-attached
 * after a page reload. Past runs are listed and reopen their results and paywalled queue.
 */
export function HarvesterPanel() {
  const queryClient = useQueryClient();
  const live = useLive();
  const schemas = useSchemas();
  const { categories: folders } = useLibraryCategories(true);
  const createCategory = useCreateCategory();

  const [topic, setTopic] = useState('');
  const [choice, setChoice] = useState('');
  const [newName, setNewName] = useState('');
  const [schema, setSchema] = useState('');
  const [sources, setSources] = useState<HarvestSource[]>(HARVEST_SOURCES.map(source => source.id));
  const [maxDownloads, setMaxDownloads] = useState(String(MAX_DOWNLOADS_DEFAULT));

  const [watch, setWatch] = useState<{ id: string; topic: string | null } | null>(null);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [openRunId, setOpenRunId] = useState<string | null>(null);

  const running = useRunningHarvest(live);
  useEffect(() => {
    if (!watch && running.data && running.data.id !== dismissed) {
      setWatch({ id: running.data.id, topic: null });
      setOpenRunId(running.data.id);
    }
  }, [running.data, watch, dismissed]);

  const job = useJob(watch?.id ?? null, FAST_POLL_MS, live);
  const terminal = job.data ? TERMINAL_JOB_STATES.has(job.data.state) : false;
  const active = watch !== null && !terminal;
  const logs = useJobLogPolling(watch?.id ?? null, { live, terminal });
  // The log of an opened past run (the watched run shows its own above); a finished job's log is fetched once.
  const pastLogs = useJobLogPolling(openRunId !== null && openRunId !== watch?.id ? openRunId : null, { live, terminal: true });
  const cancel = useCancelJob();
  const start = useStartHarvest();
  const runs = useHarvestRuns(true, live && active ? POLL_MS : false);
  const candidates = useHarvestCandidates(openRunId, live && active ? POLL_MS : false);
  const fileMutation = useFileHarvestPdf();
  const dismissMutation = useDismissCandidate();

  // React once when the followed run reaches a terminal state.
  const reported = useRef<string | null>(null);
  useEffect(() => {
    if (!watch || !job.data || !TERMINAL_JOB_STATES.has(job.data.state) || reported.current === watch.id) return;
    reported.current = watch.id;
    if (job.data.state === 'succeeded') toast.success('Harvest finished.');
    else toast.error(`Harvest ${job.data.state}${job.data.error ? `: ${job.data.error}` : ''}`);
    void queryClient.invalidateQueries({ queryKey: harvestKeys.all });
    void queryClient.invalidateQueries({ queryKey: libraryKeys.all });
  }, [watch, job.data, queryClient]);

  const category = choice === NEW_CATEGORY ? (isValidFolderName(newName.trim()) ? newName.trim() : null) : choice;
  const maxValue = maxDownloads.trim() === '' ? Number.NaN : Number(maxDownloads);
  const problem = harvestFormProblem({ topic, category, sources, maxDownloads: maxValue });
  const canStart = problem === null && !start.isPending;
  const creating = choice === NEW_CATEGORY;
  const newNameInvalid = creating && newName.trim() !== '' && !isValidFolderName(newName.trim());
  const idPrefix = useId();
  const id = (name: string) => `${idPrefix}-${name}`;
  const schemaList = schemas.data?.schemas ?? [];
  const runTopic = (runId: string | null) => runs.data?.runs.find(run => run.id === runId)?.topic ?? null;

  function handleStart() {
    if (!canStart || category === null) return;
    const trimmed = topic.trim();
    start.mutate(
      {
        topic: trimmed,
        category,
        ...(schema ? { schema } : {}),
        sources,
        max_downloads: maxValue,
        nonce: newNonce(),
      },
      {
        onSuccess: ref => {
          setWatch({ id: ref.id, topic: trimmed });
          setOpenRunId(ref.id);
          setDismissed(null);
        },
        onError: error => toast.error(error.message),
      },
    );
  }

  const busyId = fileMutation.isPending ? fileMutation.variables.candidate.id
    : dismissMutation.isPending ? dismissMutation.variables.id : null;

  function handleFile(candidate: HarvestCandidate, file: File) {
    fileMutation.mutate({ candidate, file }, {
      onSuccess: result => {
        if (result.duplicate) toast.info(`${file.name} is already in the library (${result.paper.relpath}).`);
        else toast.success(`Filed ${file.name} as ${result.paper.relpath}.`);
        if (result.manifest && !result.manifest.updated && result.manifest.detail !== 'already listed in the manifest') {
          toast.warning(result.manifest.detail);
        }
      },
      onError: error => toast.error(error.message),
    });
  }

  const form = (
    <form
      aria-label="Start a harvest"
      className="space-y-4"
      onSubmit={event => {
        event.preventDefault();
        handleStart();
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1 sm:col-span-2">
          <Label htmlFor={id('topic')}>Topic</Label>
          <Input
            id={id('topic')}
            value={topic}
            maxLength={TOPIC_MAX_CHARS + 20}
            aria-invalid={topic.trim().length > TOPIC_MAX_CHARS}
            placeholder="e.g. resonant soft X-ray scattering of polymer blends"
            onChange={event => setTopic(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            Searched on the chosen sources; papers are ranked by how many topic terms they contain.
          </p>
        </div>
        <div className="space-y-1">
          <Label htmlFor={id('category')}>Category</Label>
          <select
            id={id('category')}
            value={choice}
            onChange={event => setChoice(event.target.value)}
            className={SELECT_CLASS}
          >
            <option value="">Choose a category…</option>
            {folders.map(folder => (
              <option key={folder.name} value={folder.name}>{folder.name}</option>
            ))}
            <option value={NEW_CATEGORY}>New category…</option>
          </select>
          <p className="text-xs text-muted-foreground">Papers are filed under the category by publication year.</p>
        </div>
        {creating ? (
          <NewCategoryField
            value={newName}
            onChange={setNewName}
            existing={folders.map(folder => folder.name)}
            onCreate={createCategory.mutateAsync}
            onCreated={name => {
              setChoice(name);
              setNewName('');
            }}
          />
        ) : null}
        <div className="space-y-1">
          <Label htmlFor={id('schema')}>Schema (label only)</Label>
          <select
            id={id('schema')}
            value={schema}
            disabled={schemas.isPending}
            onChange={event => setSchema(event.target.value)}
            className={SELECT_CLASS}
          >
            <option value="">None</option>
            {schemaList.map(entry => (
              <option key={entry.name} value={entry.name}>
                {entry.label}{entry.version ? ` (${entry.version})` : ''}
              </option>
            ))}
          </select>
          <p className="text-xs text-muted-foreground">
            Recorded with the run so Extraction can default to it; it does not change the search.
          </p>
        </div>
        <div className="space-y-1">
          <Label htmlFor={id('max')}>Max downloads</Label>
          <Input
            id={id('max')}
            type="number"
            inputMode="numeric"
            min={MAX_DOWNLOADS_MIN}
            max={MAX_DOWNLOADS_MAX}
            value={maxDownloads}
            aria-invalid={!Number.isInteger(maxValue) || maxValue < MAX_DOWNLOADS_MIN || maxValue > MAX_DOWNLOADS_MAX}
            onChange={event => setMaxDownloads(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            Open-access papers downloaded, best matches first ({MAX_DOWNLOADS_MIN}-{MAX_DOWNLOADS_MAX}).
          </p>
        </div>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Sources</legend>
        <div className="flex flex-wrap gap-6">
          {HARVEST_SOURCES.map(source => (
            <div key={source.id} className="flex items-center gap-2">
              <Checkbox
                id={id(`source-${source.id}`)}
                checked={sources.includes(source.id)}
                onCheckedChange={checked =>
                  setSources(current => (checked === true
                    ? [...current.filter(entry => entry !== source.id), source.id]
                    : current.filter(entry => entry !== source.id)))}
              />
              <Label htmlFor={id(`source-${source.id}`)}>{source.label}</Label>
              {source.id === 'openalex' && runs.data?.openalex_keyed === false ? (
                <span className="text-xs text-muted-foreground" data-testid="openalex-unkeyed">
                  running without a key, reduced rate
                </span>
              ) : null}
            </div>
          ))}
        </div>
      </fieldset>

      <div className="flex items-center gap-3">
        <Button type="submit" disabled={!canStart} className="disabled:cursor-not-allowed disabled:opacity-50">
          {start.isPending ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Play aria-hidden="true" />}
          Start harvest
        </Button>
        {problem !== null && (topic !== '' || choice !== '') ? (
          <p className="text-xs text-muted-foreground" data-testid="harvest-problem">{problem}</p>
        ) : null}
      </div>
    </form>
  );

  let top;
  if (watch && job.data) {
    top = (
      <HarvestRunView
        job={job.data}
        topic={watch.topic ?? runTopic(watch.id)}
        lines={logs.lines}
        logTotal={logs.total}
        polling={logs.polling}
        logError={logs.error}
        cancelling={cancel.isPending}
        onCancel={() =>
          cancel.mutate(watch.id, {
            onSuccess: () => void queryClient.invalidateQueries({ queryKey: harvestKeys.running }),
            onError: error => toast.error(error.message),
          })}
        onClose={() => {
          setDismissed(watch.id);
          setWatch(null);
          void queryClient.invalidateQueries({ queryKey: harvestKeys.running });
        }}
      />
    );
  } else if ((watch && job.isPending) || running.isPending) {
    top = (
      <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
        <Loader2 aria-hidden="true" className="animate-spin" /> Loading…
      </p>
    );
  } else {
    top = form;
  }

  return (
    <div className="flex flex-col gap-6" data-testid="harvester-panel">
      {top}

      <section aria-labelledby="harvest-runs-title" className="space-y-2">
        <h3 id="harvest-runs-title" className="text-sm font-semibold">Runs</h3>
        {runs.isError ? (
          <p role="alert" className="text-sm text-destructive">{runs.error.message}</p>
        ) : runs.isPending ? (
          <p role="status" className="text-sm text-muted-foreground">Loading runs…</p>
        ) : (
          <HarvestRunsList runs={runs.data.runs} selectedId={openRunId} onSelect={setOpenRunId} />
        )}
      </section>

      {openRunId !== null ? (
        <section aria-labelledby="harvest-candidates-title" className="space-y-2">
          <h3 id="harvest-candidates-title" className="text-sm font-semibold">
            Papers found{runTopic(openRunId) ? ` for “${runTopic(openRunId)}”` : ''}
            {candidates.data ? ` (${candidates.data.candidates.length})` : ''}
          </h3>
          {candidates.isError ? (
            <p role="alert" className="text-sm text-destructive">{candidates.error.message}</p>
          ) : candidates.isPending ? (
            <p role="status" className="text-sm text-muted-foreground">Loading papers…</p>
          ) : (
            <HarvestResults
              candidates={candidates.data.candidates}
              busyId={busyId}
              onFile={handleFile}
              onDismiss={candidate =>
                dismissMutation.mutate(candidate, { onError: error => toast.error(error.message) })}
              onRejected={names => toast.error(`Only PDF files can be filed. Rejected: ${names.join(', ')}`)}
            />
          )}
        </section>
      ) : null}

      {openRunId !== null && openRunId !== watch?.id ? (
        <section aria-labelledby="harvest-log-title" className="space-y-2">
          <h3 id="harvest-log-title" className="text-sm font-semibold">
            Run log{pastLogs.total > 0 ? ` (${pastLogs.total} lines)` : ''}
          </h3>
          <RunLog lines={pastLogs.lines} total={pastLogs.total} polling={pastLogs.polling} error={pastLogs.error} />
        </section>
      ) : null}
    </div>
  );
}
