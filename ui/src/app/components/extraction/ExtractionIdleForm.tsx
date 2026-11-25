import { Calculator, Download, Loader2, Play } from 'lucide-react';
import { useMemo, useState } from 'react';
import { toast } from 'sonner';
import {
  condensedSchemaNote,
  isUserSchemaChoice,
  isValidKgName,
  type EstimateResponse,
  type KgBundleSummary,
} from '../extractionApi';
import {
  useContinueKg,
  useEstimateExtraction,
  useExportKg,
  useExtractionModels,
  useKgBundles,
  useSchemas,
  useStartExtraction,
} from '../extractionHooks';
import { useLibraryPapers } from '../libraryHooks';
import { errorWithReason } from '../providerErrors';
import { buildModelChoices, modelSourceNote, noBackendNote } from './modelChoices';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { RadioGroup, RadioGroupItem } from '../ui/radio-group';
import { ExportedNotice } from './ExportedNotice';
import { FindSupportingText } from './FindSupportingText';
import { PaperPicker } from './PaperPicker';
import { PublishToTiled } from './PublishToTiled';
import { SchemaPanelButton } from '../schema/SchemaPanelButton';

/** Props of {@link ExtractionIdleForm}. */
export interface ExtractionIdleFormProps {
  /** Called with the new job id and bundle name once the job was submitted. */
  onStarted: (jobId: string, kgName: string) => void;
}

type Mode = 'new' | 'continue';

const SELECT_CLASS =
  'h-9 w-full min-w-0 rounded-md border border-border bg-background px-3 text-sm shadow-sm outline-none '
  + 'focus:border-ring disabled:cursor-not-allowed disabled:opacity-50';

const newNonce = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

/** `value` of the continue picker: `bundle:<name>` or `source:<file>`. */
const bundleValue = (name: string) => `bundle:${name}`;
const sourceValue = (file: string) => `source:${file}`;

/**
 * Idle view of the Extraction tab: continue an existing KG or start a new one, topic, schema (locked
 * when continuing a bundle), paper picker, model picker (live list from `GET /v1/library/models`,
 * never the Settings model), KG name, cost estimate with explicit confirmation, and Start.
 * Start stays disabled until the name is valid, at least one paper is chosen, a model is picked and the
 * cost has been confirmed for exactly this selection.
 */
export function ExtractionIdleForm({ onStarted }: ExtractionIdleFormProps) {
  const bundles = useKgBundles();
  const schemas = useSchemas();
  const models = useExtractionModels();
  const papers = useLibraryPapers(true);

  const estimate = useEstimateExtraction();
  const continueKg = useContinueKg();
  const start = useStartExtraction();
  const exportKg = useExportKg();

  const [mode, setMode] = useState<Mode>('new');
  const [parentValue, setParentValue] = useState('');
  const [topic, setTopic] = useState('');
  const [schemaChoice, setSchemaChoice] = useState('');
  const [model, setModel] = useState('');
  const [name, setName] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmed, setConfirmed] = useState(false);
  const [estimated, setEstimated] = useState<{ key: string; result: EstimateResponse } | null>(null);

  const bundleList = bundles.data?.bundles ?? [];
  const sourceList = bundles.data?.sources ?? [];
  const parentBundle: KgBundleSummary | undefined = parentValue.startsWith('bundle:')
    ? bundleList.find(b => bundleValue(b.name) === parentValue)
    : undefined;
  const parentSource = parentValue.startsWith('source:') ? parentValue.slice('source:'.length) : null;
  const continuing = mode === 'continue';
  const schemaLocked = continuing && parentBundle !== undefined;
  const schemaList = schemas.data?.schemas ?? [];
  const schema = schemaLocked ? parentBundle.schema : schemaChoice || schemaList[0]?.name || '';

  const shippedSchemas = schemaList.filter(s => !isUserSchemaChoice(s));
  const userSchemas = schemaList.filter(isUserSchemaChoice);
  const schemaIsUser = schema.startsWith('user/');

  const choices = useMemo(() => buildModelChoices(models.data), [models.data]);
  const choice = choices.find(c => c.value === model) ?? choices[0];
  const selectedModel = choice?.model ?? '';
  const selectedBackend = choice?.backend;
  const noBackend = models.isSuccess ? choices.length === 0 : models.isError;

  const promptNote = schemaIsUser ? condensedSchemaNote(estimated?.result.schema_prompt, selectedBackend ?? 'cborg') : null;
  const nameValid = isValidKgName(name);
  const nameTaken = bundleList.some(b => b.name === name);
  const parentReady = !continuing || parentValue !== '';
  const estimateKey = JSON.stringify([[...selected].sort(), choice?.value ?? '', schema]);
  const estimateCurrent = estimated !== null && estimated.key === estimateKey;
  const costConfirmed = confirmed && estimateCurrent;
  const canEstimate = selected.size > 0 && selectedModel !== '' && schema !== '' && parentReady;
  const canStart = nameValid && !nameTaken && selected.size > 0 && costConfirmed && parentReady && !start.isPending
    && !continueKg.isPending;

  const selectedPapers = useMemo(() => [...selected], [selected]);

  const reset = () => {
    setConfirmed(false);
    setEstimated(null);
  };
  const changeSelection = (next: Set<string>) => {
    setSelected(next);
    setConfirmed(false);
  };

  async function handleEstimate() {
    try {
      const result = await estimate.mutateAsync({ paper_ids: selectedPapers, model: selectedModel, schema });
      setEstimated({ key: estimateKey, result });
      setConfirmed(false);
    } catch (error) {
      toast.error(errorWithReason(error, 'The estimate failed.'));
    }
  }

  async function handleStart() {
    if (!estimated) return;
    try {
      if (continuing) {
        const created = await continueKg.mutateAsync({
          name,
          ...(parentBundle ? { parent: parentBundle.name } : { source: parentSource ?? '', schema }),
          ...(topic.trim() ? { topic: topic.trim() } : {}),
        });
        for (const warning of created.warnings ?? []) toast.warning(warning);
      }
      const job = await start.mutateAsync({
        bundle: name,
        topic: topic.trim() || null,
        schema,
        model: selectedModel,
        ...(selectedBackend ? { backend: selectedBackend } : {}),
        papers: selectedPapers,
        confirmation: estimated.result.confirmation,
        nonce: newNonce(),
      });
      onStarted(job.id, name);
    } catch (error) {
      toast.error(errorWithReason(error, 'Could not start the extraction.'));
    }
  }

  async function handleExport() {
    if (!parentBundle) return;
    try {
      const result = await exportKg.mutateAsync(parentBundle.name);
      toast.success(`Exported ${result.file_name}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Export failed.');
    }
  }

  return (
    <form
      className="space-y-6"
      data-testid="extraction-idle"
      onSubmit={event => {
        event.preventDefault();
        if (canStart) void handleStart();
      }}
    >
      {noBackend && (
        <p role="alert" className="rounded-md border border-border bg-muted p-3 text-sm" data-testid="no-backend">
          {noBackendNote(models.data)}
        </p>
      )}

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Knowledge graph</legend>
        <RadioGroup
          aria-label="Continue or new"
          value={mode}
          onValueChange={value => {
            setMode(value as Mode);
            reset();
          }}
          className="flex gap-6"
        >
          <div className="flex items-center gap-2">
            <RadioGroupItem value="new" id="mode-new" />
            <Label htmlFor="mode-new">Start new KG</Label>
          </div>
          <div className="flex items-center gap-2">
            <RadioGroupItem value="continue" id="mode-continue" />
            <Label htmlFor="mode-continue">Continue existing KG</Label>
          </div>
        </RadioGroup>
        {continuing && (
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="Existing KG"
              value={parentValue}
              onChange={event => {
                setParentValue(event.target.value);
                reset();
              }}
              className={SELECT_CLASS}
            >
              <option value="">Choose a KG</option>
              {bundleList.map(b => (
                <option key={b.name} value={bundleValue(b.name)}>{b.name}</option>
              ))}
              {sourceList.map(file => (
                <option key={file} value={sourceValue(file)}>{file} (KG file)</option>
              ))}
            </select>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!parentBundle || exportKg.isPending}
              onClick={() => void handleExport()}
              className="disabled:cursor-not-allowed disabled:opacity-50"
            >
              {exportKg.isPending ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Download aria-hidden="true" />}
              Export
            </Button>
            <PublishToTiled name={parentBundle?.name ?? null} />
            <FindSupportingText name={parentBundle?.name ?? null} />
          </div>
        )}
        {continuing && exportKg.isSuccess && exportKg.data.name === parentBundle?.name ? (
          <ExportedNotice result={exportKg.data} />
        ) : null}
      </fieldset>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor="kg-name">{continuing ? 'New KG name' : 'KG name'}</Label>
          <Input
            id="kg-name"
            value={name}
            maxLength={64}
            aria-invalid={name !== '' && (!nameValid || nameTaken)}
            aria-describedby="kg-name-help"
            onChange={event => setName(event.target.value)}
          />
          <p
            id="kg-name-help"
            className={name !== '' && (!nameValid || nameTaken) ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}
          >
            {nameTaken
              ? 'A KG with this name already exists.'
              : 'Letters, digits, "_" and "-"; start with a letter or digit; up to 64 characters.'}
          </p>
        </div>
        <div className="space-y-1">
          <Label htmlFor="kg-topic">Topic</Label>
          <Input id="kg-topic" value={topic} maxLength={200} onChange={event => setTopic(event.target.value)} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="kg-schema">Schema</Label>
          <select
            id="kg-schema"
            value={schema}
            disabled={schemaLocked || schemas.isPending}
            onChange={event => {
              setSchemaChoice(event.target.value);
              reset();
            }}
            className={SELECT_CLASS}
          >
            {schemaLocked && !schemaList.some(s => s.name === schema) && <option value={schema}>{schema}</option>}
            {shippedSchemas.map(s => (
              <option key={s.name} value={s.name}>{s.label}{s.version ? ` (${s.version})` : ''}</option>
            ))}
            {userSchemas.length > 0 && (
              <optgroup label="Your schemas (user schema)">
                {userSchemas.map(s => <option key={s.name} value={s.name}>{s.label}</option>)}
              </optgroup>
            )}
          </select>
          {schemaLocked && <p className="text-xs text-muted-foreground">Locked to the schema of the KG being continued.</p>}
          {schemaIsUser && <p className="text-xs text-muted-foreground" data-testid="user-schema-note">This is a user schema: written here, not reviewed, and it cannot be published yet.</p>}
          {promptNote && estimateCurrent && <p role="status" className="text-xs" data-testid="schema-prompt-note">{promptNote}</p>}
          {!schemaLocked && <SchemaPanelButton modelValue={choice?.value} onSaved={id => { setSchemaChoice(id); reset(); }} />}
        </div>
        <div className="space-y-1">
          <Label htmlFor="kg-model">Model</Label>
          <select
            id="kg-model"
            value={choice?.value ?? ''}
            disabled={choices.length === 0}
            onChange={event => {
              setModel(event.target.value);
              reset();
            }}
            className={SELECT_CLASS}
          >
            {choices.length === 0 && <option value="">No models available</option>}
            {(['CBORG', 'Ollama'] as const).map(group => {
              const inGroup = choices.filter(c => c.group === group);
              if (inGroup.length === 0) return null;
              // CBORG-only deployments keep the flat list; a second provider adds labelled groups.
              const options = inGroup.map(c => <option key={c.value} value={c.value}>{c.model}</option>);
              return choices.some(c => c.group !== group)
                ? <optgroup key={group} label={group === 'Ollama' ? 'Ollama (local server)' : 'CBORG (free models)'}>{options}</optgroup>
                : options;
            })}
          </select>
          <p className="text-xs text-muted-foreground" data-testid="model-source">
            {modelSourceNote(models.data)}
          </p>
        </div>
      </div>

      <div className="space-y-1">
        <Label>Papers</Label>
        <PaperPicker papers={papers.data?.papers ?? []} selected={selected} onChange={changeSelection} />
      </div>

      <div className="space-y-3 rounded-md border border-border p-3">
        <div className="flex items-center gap-3">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!canEstimate || estimate.isPending}
            onClick={() => void handleEstimate()}
            className="disabled:cursor-not-allowed disabled:opacity-50"
          >
            {estimate.isPending ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Calculator aria-hidden="true" />}
            Estimate cost
          </Button>
          {estimateCurrent && estimated && (
            <p className="text-sm" data-testid="estimate-summary">
              {estimated.result.paper_count ?? selected.size} papers, {estimated.result.pages} pages,{' '}
              {estimated.result.estimated_cost_usd === null
                ? 'cost unknown'
                : `about $${estimated.result.estimated_cost_usd.toFixed(2)}`}
              {estimated.result.unknown_pages ? ` (${estimated.result.unknown_pages} papers with unknown page count)` : ''}
            </p>
          )}
          {estimated && !estimateCurrent && (
            <p className="text-sm text-muted-foreground">Selection changed; estimate again.</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Checkbox
            id="confirm-cost"
            checked={costConfirmed}
            disabled={!estimateCurrent}
            onCheckedChange={checked => setConfirmed(checked === true)}
            className="disabled:cursor-not-allowed disabled:opacity-50"
          />
          <Label htmlFor="confirm-cost" className={estimateCurrent ? '' : 'opacity-50'}>
            I confirm this cost estimate
          </Label>
        </div>
      </div>

      <Button type="submit" disabled={!canStart} className="disabled:cursor-not-allowed disabled:opacity-50">
        {start.isPending || continueKg.isPending ? <Loader2 aria-hidden="true" className="animate-spin" /> : <Play aria-hidden="true" />}
        Start extraction
      </Button>
    </form>
  );
}
