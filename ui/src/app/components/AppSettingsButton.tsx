import { useEffect, useState } from 'react';
import * as RadioGroupPrimitive from '@radix-ui/react-radio-group';
import { ButtonWithIcon } from '@blueskyproject/finch';
import { Check, ChevronDown, Info, Save, Settings } from 'lucide-react';
import {
  DEFAULT_AGENT_SETTINGS,
  DEFAULT_JSON_GRAPH_PATHS,
  DEFAULT_OLLAMA_MODEL,
  KG_QUERY_HOPS_PRESETS,
  KG_QUERY_MAX_NODES_PRESETS,
  defaultModelForBackend,
  loadAgentSettings,
  saveAgentSettings,
  settingsEqual,
  settingsFromApiResponse,
  settingsToApiPayload,
  type AgentBackend,
  type AgentExtractionMode,
  type AgentGraphSource,
  type AgentSettings,
  type AgentWorkflowMode,
} from './agentSettings';
import { AppErrorMessage } from './AppErrorMessage';
import { AsciiOrb } from './AsciiOrb';
import { fetchAgentSettings, updateAgentSettings } from './data/liveAgent';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { Label } from './ui/label';
import { RadioGroup } from './ui/radio-group';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from './ui/sheet';
import { cn } from './ui/utils';

function formatJsonGraphLabel(path: string): string {
  return path.replace(/^storage\/kg\//, '');
}

function formatCborgModelLabel(model: string): string {
  const slash = model.lastIndexOf('/');
  return slash === -1 ? model : model.slice(slash + 1);
}

function graphHint(path: string): string {
  if (path.includes('matkg_rsoxs_v1')) return 'literature / science';
  if (path.includes('matkg_bl1101')) return '11.0.1.2 ops';
  if (path.includes('matkg_xray_papers_cborg_chat')) return 'x-ray demo (opt-in)';
  return 'catalog graph';
}

function CborgModelPickerDialog({
  open,
  onOpenChange,
  value,
  options,
  onChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  value: string;
  options: string[];
  onChange: (model: string) => void;
}) {
  function handlePick(model: string) {
    onChange(model);
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange} modal>
      <DialogContent
        overlayClassName="z-[200] data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0"
        className="z-[201] top-[88px] left-1/2 flex max-h-[calc(100vh-7rem)] w-[calc(100%-2rem)] max-w-md -translate-x-1/2 translate-y-0 flex-col gap-0 overflow-hidden border border-slate-200 bg-white p-0 text-slate-800 shadow-xl"
      >
        <DialogHeader className="border-b border-slate-200 bg-white px-5 py-5 text-left">
          <DialogTitle className="text-slate-900">Choose CBORG model</DialogTitle>
          <DialogDescription className="text-slate-500">
            Select a hosted model from the CBORG API.
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto bg-white p-4">
          {options.map(model => {
            const selected = model === value;
            return (
              <button
                key={model}
                type="button"
                onClick={() => handlePick(model)}
                className={cn(
                  'flex w-full items-center gap-3 rounded-lg px-4 py-3.5 text-left text-sm transition hover:bg-slate-50',
                  selected && 'bg-sky-50 ring-1 ring-sky-200',
                )}
              >
                <span
                  className={cn(
                    'flex size-4 shrink-0 items-center justify-center rounded-full border border-slate-300',
                    selected && 'border-sky-500 bg-sky-500 text-white',
                  )}
                  aria-hidden="true"
                >
                  {selected && <Check size={12} strokeWidth={3} />}
                </span>
                <span className="min-w-0">
                  <span className="block truncate font-medium text-slate-800">
                    {formatCborgModelLabel(model)}
                  </span>
                  <span className="mt-1 block truncate text-xs text-slate-500">{model}</span>
                </span>
              </button>
            );
          })}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function CborgModelPicker({
  value,
  disabled,
  onOpenPicker,
}: {
  value: string;
  disabled?: boolean;
  onOpenPicker: () => void;
}) {
  const selectedLabel = value ? formatCborgModelLabel(value) : 'Select a CBORG model';

  return (
    <button
      type="button"
      id="cborg-model-select"
      disabled={disabled}
      onClick={onOpenPicker}
      className="flex h-9 w-full items-center justify-between gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
    >
      <span className="truncate font-medium">{selectedLabel}</span>
      <ChevronDown size={16} className="shrink-0 text-slate-500" aria-hidden="true" />
    </button>
  );
}

function SettingOption({
  id,
  label,
  description,
}: {
  id: string;
  label: string;
  description: string;
}) {
  return (
    <label htmlFor={id} className="flex cursor-pointer items-start gap-3 rounded-lg px-3 py-2.5 hover:bg-slate-50">
      <RadioGroupPrimitive.Item
        id={id}
        value={id}
        className="mt-0.5 flex size-4 shrink-0 items-center justify-center overflow-hidden outline-none disabled:cursor-not-allowed disabled:opacity-50"
      >
        <RadioGroupPrimitive.Indicator className="flex items-center justify-center">
          <AsciiOrb size={16} className="text-sky-400" interactive={false} />
        </RadioGroupPrimitive.Indicator>
      </RadioGroupPrimitive.Item>
      <span className="min-w-0">
        <span className="block text-sm font-medium text-slate-800">{label}</span>
        <span className="mt-0.5 block text-xs leading-relaxed text-slate-500">{description}</span>
      </span>
    </label>
  );
}

export function AppSettingsButton({
  onSettingsApplied,
}: {
  onSettingsApplied?: () => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [savedSettings, setSavedSettings] = useState<AgentSettings>(() => loadAgentSettings());
  const [draftSettings, setDraftSettings] = useState<AgentSettings>(() => loadAgentSettings());
  const [availableJsonGraphs, setAvailableJsonGraphs] = useState<string[]>([]);
  const [availableCborgModels, setAvailableCborgModels] = useState<string[]>([]);
  const [defaultOllamaModel, setDefaultOllamaModel] = useState(DEFAULT_OLLAMA_MODEL);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [errorTitle, setErrorTitle] = useState('Settings update failed');
  const [cborgPickerOpen, setCborgPickerOpen] = useState(false);
  const [tiledStatus, setTiledStatus] = useState<string | undefined>();
  const [tiledApiKeySet, setTiledApiKeySet] = useState(false);
  const [tiledError, setTiledError] = useState<string | null>(null);

  const hasUnsavedChanges = !settingsEqual(draftSettings, savedSettings);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError('');
      setErrorTitle('Settings unavailable');
      const saved = loadAgentSettings();
      setSavedSettings(saved);
      setDraftSettings(saved);
      try {
        const response = await fetchAgentSettings();
        if (cancelled) return;
        setAvailableJsonGraphs(response.available_json_graphs ?? []);
        setAvailableCborgModels(response.available_cborg_models ?? []);
        setDefaultOllamaModel(response.default_ollama_model || DEFAULT_OLLAMA_MODEL);
        setTiledStatus(response.tiled_status);
        setTiledApiKeySet(response.tiled_api_key_set === true);
        setTiledError(response.tiled_error ?? null);
        const synced = settingsFromApiResponse(response);
        setDraftSettings(prev => ({
          ...prev,
          useLiveTiled: synced.useLiveTiled,
          tiledUri: synced.tiledUri || prev.tiledUri,
        }));
        setSavedSettings(prev => ({
          ...prev,
          useLiveTiled: synced.useLiveTiled,
          tiledUri: synced.tiledUri || prev.tiledUri,
        }));
        if (!response.available_json_graphs?.includes(saved.jsonGraphPath) && synced.jsonGraphPath) {
          setDraftSettings(prev => ({
            ...prev,
            jsonGraphPath: saved.jsonGraphPath || synced.jsonGraphPath,
          }));
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
          setAvailableJsonGraphs([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [open]);

  function handleOpenChange(nextOpen: boolean) {
    if (!nextOpen && !saving) {
      setDraftSettings(savedSettings);
      setError('');
    }
    setOpen(nextOpen);
  }

  async function handleSave() {
    setSaving(true);
    setError('');
    setErrorTitle('Settings update failed');
    try {
      const response = await updateAgentSettings(settingsToApiPayload(draftSettings));
      const synced = settingsFromApiResponse(response);
      const saved: AgentSettings = {
        backend: draftSettings.backend,
        model: draftSettings.model,
        graphSource: draftSettings.graphSource,
        workflowMode: draftSettings.workflowMode,
        extractionMode: draftSettings.extractionMode,
        targetedMaxPages: draftSettings.targetedMaxPages,
        jsonGraphPath: draftSettings.graphSource === 'json'
          ? draftSettings.jsonGraphPath
          : synced.jsonGraphPath,
        jsonGraphPaths: draftSettings.graphSource === 'json'
          ? draftSettings.jsonGraphPaths
          : synced.jsonGraphPaths,
        kgQueryMaxNodes: draftSettings.kgQueryMaxNodes,
        kgQueryHops: draftSettings.kgQueryHops,
        sourceRag: draftSettings.sourceRag,
        useLiveTiled: draftSettings.useLiveTiled,
        tiledUri: draftSettings.tiledUri,
      };
      setSavedSettings(saved);
      setDraftSettings(saved);
      saveAgentSettings(saved);
      setAvailableJsonGraphs(response.available_json_graphs ?? []);
      setAvailableCborgModels(response.available_cborg_models ?? []);
      setDefaultOllamaModel(response.default_ollama_model || DEFAULT_OLLAMA_MODEL);
      setTiledStatus(response.tiled_status);
      setTiledApiKeySet(response.tiled_api_key_set === true);
      setTiledError(response.tiled_error ?? null);
      await onSettingsApplied?.();
      setCborgPickerOpen(false);
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  function updateBackend(backend: AgentBackend) {
    setDraftSettings(prev => ({
      ...prev,
      backend,
      model: backend === prev.backend
        ? prev.model
        : defaultModelForBackend(backend, {
          default_ollama_model: defaultOllamaModel,
          available_cborg_models: availableCborgModels,
        }),
    }));
  }

  function updateModel(model: string) {
    setDraftSettings(prev => ({ ...prev, model }));
  }

  function updateGraphSource(graphSource: AgentGraphSource) {
    const jsonGraphPaths = draftSettings.jsonGraphPaths.length
      ? draftSettings.jsonGraphPaths
      : availableJsonGraphs.filter(path => DEFAULT_JSON_GRAPH_PATHS.includes(path as typeof DEFAULT_JSON_GRAPH_PATHS[number]));
    const fallback = jsonGraphPaths.length
      ? jsonGraphPaths
      : [...DEFAULT_AGENT_SETTINGS.jsonGraphPaths];
    setDraftSettings(prev => ({
      ...prev,
      graphSource,
      jsonGraphPaths: fallback,
      jsonGraphPath: fallback.includes(prev.jsonGraphPath) ? prev.jsonGraphPath : fallback[0],
    }));
  }

  function updateWorkflowMode(workflowMode: AgentWorkflowMode) {
    setDraftSettings(prev => ({ ...prev, workflowMode }));
  }

  function updateExtractionMode(extractionMode: AgentExtractionMode) {
    setDraftSettings(prev => ({ ...prev, extractionMode }));
  }

  function updateTargetedMaxPages(value: string) {
    const parsed = Number.parseInt(value, 10);
    setDraftSettings(prev => ({
      ...prev,
      targetedMaxPages: Number.isFinite(parsed)
        ? Math.min(100, Math.max(1, parsed))
        : prev.targetedMaxPages,
    }));
  }

  function toggleJsonGraphPath(path: string) {
    setDraftSettings(prev => {
      const selected = prev.jsonGraphPaths.includes(path)
        ? prev.jsonGraphPaths.filter(item => item !== path)
        : [...prev.jsonGraphPaths, path];
      if (!selected.length) return prev;
      const jsonGraphPath = selected.includes(prev.jsonGraphPath) ? prev.jsonGraphPath : selected[0];
      return { ...prev, graphSource: 'json', jsonGraphPaths: selected, jsonGraphPath };
    });
  }

  function updateDisplayGraph(jsonGraphPath: string) {
    setDraftSettings(prev => {
      const jsonGraphPaths = prev.jsonGraphPaths.includes(jsonGraphPath)
        ? prev.jsonGraphPaths
        : [...prev.jsonGraphPaths, jsonGraphPath];
      return { ...prev, graphSource: 'json', jsonGraphPath, jsonGraphPaths };
    });
  }

  return (
    <>
      <ButtonWithIcon
        text="Settings"
        icon={<Settings size={16} strokeWidth={2} aria-hidden="true" />}
        isSecondary
        size="small"
        aria-label="Settings"
        onClick={() => setOpen(true)}
      />
      <Sheet open={open} onOpenChange={handleOpenChange}>
        <SheetContent
          side="right"
          className="flex w-full flex-col gap-0 bg-white p-0 text-slate-800 sm:max-w-xl"
        >
          <SheetHeader className="border-b border-slate-200">
            <SheetTitle>Settings</SheetTitle>
            <SheetDescription>FAIR2WISE agent configuration</SheetDescription>
          </SheetHeader>

          <div className="relative min-h-0 flex-1">
            <div className="h-full space-y-6 overflow-y-auto px-4 py-4 pb-20 text-sm">
            <div className="space-y-3">
              <Label className="text-xs font-medium text-slate-500">Workflow</Label>
              <RadioGroup
                value={draftSettings.workflowMode}
                onValueChange={value => updateWorkflowMode(value as AgentWorkflowMode)}
                className="gap-2"
                disabled={loading || saving}
              >
                <SettingOption
                  id="agentic"
                  label="Agentic"
                  description="Debate evidence sufficiency before downloading and extracting papers."
                />
                <SettingOption
                  id="deterministic"
                  label="Deterministic"
                  description="Use the existing retrieve, download, extract loop."
                />
              </RadioGroup>
            </div>

            <div className="space-y-3">
              <Label className="text-xs font-medium text-slate-500">Extraction</Label>
              <RadioGroup
                value={draftSettings.extractionMode}
                onValueChange={value => updateExtractionMode(value as AgentExtractionMode)}
                className="gap-2"
                disabled={loading || saving}
              >
                <SettingOption
                  id="targeted"
                  label="Targeted"
                  description="Extract only pages relevant to the missing evidence."
                />
                <SettingOption
                  id="full"
                  label="Full"
                  description="Extract every page after an approved download."
                />
              </RadioGroup>
              {draftSettings.extractionMode === 'targeted' && (
                <div className="space-y-2 px-3">
                  <Label htmlFor="targeted-max-pages" className="text-xs font-medium text-slate-500">
                    Max pages per PDF
                  </Label>
                  <input
                    id="targeted-max-pages"
                    type="number"
                    min={1}
                    max={100}
                    value={draftSettings.targetedMaxPages}
                    disabled={loading || saving}
                    onChange={event => updateTargetedMaxPages(event.target.value)}
                    className="h-9 w-28 rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-800 shadow-sm outline-none focus:border-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
                  />
                </div>
              )}
            </div>

            <div className="space-y-3">
              <Label className="text-xs font-medium text-slate-500">LLM backend</Label>
              <RadioGroup
                value={draftSettings.backend}
                onValueChange={value => updateBackend(value as AgentBackend)}
                className="gap-2"
                disabled={loading || saving}
              >
                <SettingOption
                  id="cborg"
                  label="CBORG"
                  description="Use the CBORG hosted model API (default)."
                />
                <SettingOption
                  id="ollama"
                  label="Ollama"
                  description="Use a local Ollama server for inference."
                />
              </RadioGroup>
            </div>

            {draftSettings.backend === 'cborg' ? (
              <div className="space-y-2 px-3">
                <Label htmlFor="cborg-model-select" className="text-xs font-medium text-slate-500">
                  CBORG model
                </Label>
                {availableCborgModels.length > 0 ? (
                  <CborgModelPicker
                    value={draftSettings.model}
                    disabled={loading || saving}
                    onOpenPicker={() => setCborgPickerOpen(true)}
                  />
                ) : (
                  <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                    {loading ? 'Loading CBORG models…' : 'No CBORG models configured.'}
                  </div>
                )}
              </div>
            ) : (
              <div className="space-y-2 px-3">
                <Label htmlFor="ollama-model-input" className="text-xs font-medium text-slate-500">
                  Ollama model
                </Label>
                <input
                  id="ollama-model-input"
                  type="text"
                  value={draftSettings.model}
                  disabled={loading || saving}
                  onChange={event => updateModel(event.target.value)}
                  placeholder={defaultOllamaModel}
                  className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-800 shadow-sm outline-none focus:border-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
                />
              </div>
            )}

            <div className="space-y-3">
              <Label className="text-xs font-medium text-slate-500">Knowledge graph source</Label>
              <RadioGroup
                value={draftSettings.graphSource}
                onValueChange={value => updateGraphSource(value as AgentGraphSource)}
                className="gap-2"
                disabled={loading || saving}
              >
                <SettingOption
                  id="splash_links"
                  label="splash_links"
                  description="Load the live knowledge graph from splash_links (default)."
                />
                <SettingOption
                  id="json"
                  label="JSON"
                  description="Load a MatKG JSON file from storage/kg."
                />
              </RadioGroup>
            </div>

            {draftSettings.graphSource === 'json' && (
              <div className="space-y-3">
                <Alert className="border-amber-200 bg-amber-50 text-amber-950">
                  <Info aria-hidden="true" className="text-amber-700" />
                  <AlertTitle className="text-amber-950">Retrieval only</AlertTitle>
                  <AlertDescription className="text-amber-900">
                    JSON mode is strictly for retrieval. The download and extraction agents will not run.
                    Chat queries every checked graph (fan-out × N). Selecting more KGs can slow queries.
                  </AlertDescription>
                </Alert>

                <div className="space-y-2">
                <Label className="text-xs font-medium text-slate-500">
                  JSON graphs (multi-select)
                </Label>
                {availableJsonGraphs.length > 0 ? (
                  <div className="space-y-1 rounded-lg border border-slate-200 p-2">
                    {availableJsonGraphs.map(path => {
                      const checked = draftSettings.jsonGraphPaths.includes(path);
                      const isViewer = draftSettings.jsonGraphPath === path;
                      return (
                        <div key={path} className="flex items-start gap-3 rounded-md px-2 py-2 hover:bg-slate-50">
                          <input
                            id={`json-graph-${path}`}
                            type="checkbox"
                            className="mt-1 size-4 accent-sky-600"
                            checked={checked}
                            disabled={loading || saving}
                            onChange={() => toggleJsonGraphPath(path)}
                          />
                          <label htmlFor={`json-graph-${path}`} className="min-w-0 flex-1 cursor-pointer">
                            <span className="block truncate text-sm font-medium text-slate-800">
                              {formatJsonGraphLabel(path)}
                            </span>
                            <span className="mt-0.5 block truncate text-xs text-slate-500">{graphHint(path)}</span>
                          </label>
                          <button
                            type="button"
                            disabled={loading || saving || !checked}
                            onClick={() => updateDisplayGraph(path)}
                            className={cn(
                              'shrink-0 rounded px-2 py-1 text-[10px] uppercase tracking-wide',
                              isViewer ? 'bg-sky-100 text-sky-700' : 'text-slate-400 hover:text-slate-600',
                            )}
                          >
                            {isViewer ? 'Viewer' : 'View'}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                    {loading ? 'Loading available JSON graphs…' : 'No JSON graph files found in storage/kg.'}
                  </div>
                )}
                </div>

                <div className="space-y-2">
                  <Label htmlFor="kg-query-hops" className="text-xs font-medium text-slate-500">
                    Query neighborhood hops
                  </Label>
                  <select
                    id="kg-query-hops"
                    value={draftSettings.kgQueryHops}
                    disabled={loading || saving}
                    onChange={event => setDraftSettings(prev => ({
                      ...prev,
                      kgQueryHops: Number.parseInt(event.target.value, 10) || 1,
                    }))}
                    className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-800 shadow-sm outline-none focus:border-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {KG_QUERY_HOPS_PRESETS.map(hops => (
                      <option key={hops} value={hops}>{hops} hop{hops === 1 ? '' : 's'}</option>
                    ))}
                  </select>
                  <p className="text-xs text-slate-500">
                    BFS depth used when gathering LLM context (`graph.neighborhood`). Not the viewer dump cap.
                  </p>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="kg-query-max-nodes" className="text-xs font-medium text-slate-500">
                    Query max nodes
                  </Label>
                  <select
                    id="kg-query-max-nodes"
                    value={draftSettings.kgQueryMaxNodes}
                    disabled={loading || saving}
                    onChange={event => setDraftSettings(prev => ({
                      ...prev,
                      kgQueryMaxNodes: Number.parseInt(event.target.value, 10) || 100,
                    }))}
                    className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-800 shadow-sm outline-none focus:border-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {KG_QUERY_MAX_NODES_PRESETS.map(count => (
                      <option key={count} value={count}>{count} nodes</option>
                    ))}
                  </select>
                  <p className="text-xs text-slate-500">
                    More nodes can enrich answers; too many can blow the context window or add latency. Viewer 1k/10k stays separate.
                  </p>
                </div>
              </div>
            )}

            <div className="space-y-2 rounded-lg border border-slate-200 p-3">
              <label htmlFor="live-tiled" className="flex cursor-pointer items-start gap-3">
                <input
                  id="live-tiled"
                  type="checkbox"
                  className="mt-1 size-4 accent-sky-600"
                  checked={draftSettings.useLiveTiled}
                  disabled={loading || saving}
                  onChange={event => setDraftSettings(prev => ({
                    ...prev,
                    useLiveTiled: event.target.checked,
                  }))}
                />
                <span>
                  <span className="block text-sm font-medium text-slate-800">Live Tiled Graph</span>
                  <span className="block text-xs text-slate-500">
                    ESAF / proposal / sample / scan questions query the <strong>local</strong> Tiled
                    {' '}<code>/api/graphql</code> first. JSON sim is the fallback when Tiled is down.
                    Keep <code>TILED_API_KEY</code> in <code>.env</code> — it is never shown here.
                    ALS production Tiled is not used.
                  </span>
                </span>
              </label>
              <div className="space-y-1 pl-7">
                <Label htmlFor="tiled-uri" className="text-xs font-medium text-slate-500">
                  Tiled URI
                </Label>
                <input
                  id="tiled-uri"
                  type="url"
                  value={draftSettings.tiledUri}
                  disabled={loading || saving || !draftSettings.useLiveTiled}
                  onChange={event => setDraftSettings(prev => ({
                    ...prev,
                    tiledUri: event.target.value,
                  }))}
                  placeholder="http://127.0.0.1:8001"
                  className="h-9 w-full rounded-md border border-slate-200 bg-white px-3 text-sm text-slate-800 shadow-sm outline-none focus:border-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
                />
                <p className="text-xs text-slate-500">
                  Local catalog host without <code>/api/graphql</code> (this machine uses
                  {' '}<code>127.0.0.1:8001</code>; nothing is on 8000). Keep the key in
                  {' '}<code>.env</code> — never paste it here.
                </p>
                {draftSettings.useLiveTiled && (
                  <p className="text-xs text-slate-600">
                    Status: <code>{tiledStatus || '…'}</code>
                    {' · '}API key in env: {tiledApiKeySet ? 'yes' : 'no'}
                    {tiledStatus === 'unsigned' && !tiledApiKeySet
                      ? ' · No key in .env (unsigned). GraphQL reads can be empty.'
                      : ''}
                    {tiledStatus === 'empty' && tiledApiKeySet
                      ? ' · Key is loaded; this local catalog has no ESAF/Proposal/Sample graph.'
                      : ''}
                    {tiledStatus === 'ok' && tiledApiKeySet
                      ? ' · Key is loaded; GraphQL returned identity entities.'
                      : ''}
                    {tiledError && tiledStatus !== 'ok' ? ` · ${tiledError}` : ''}
                  </p>
                )}
              </div>
            </div>

            <div className="space-y-2 rounded-lg border border-slate-200 p-3">
              <label htmlFor="source-rag" className="flex cursor-pointer items-start gap-3">
                <input
                  id="source-rag"
                  type="checkbox"
                  className="mt-1 size-4 accent-sky-600"
                  checked={draftSettings.sourceRag}
                  disabled={loading || saving}
                  onChange={event => setDraftSettings(prev => ({
                    ...prev,
                    sourceRag: event.target.checked,
                  }))}
                />
                <span>
                  <span className="block text-sm font-medium text-slate-800">Source RAG</span>
                  <span className="block text-xs text-slate-500">
                    Retrieve from papers &amp; ops docs. Off = graph-only. Hosts without PDFs should leave this off.
                  </span>
                </span>
              </label>
            </div>

            {error && (
              <AppErrorMessage title={errorTitle} className="text-xs">
                {error}
              </AppErrorMessage>
            )}
            </div>

            <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-end p-4">
              <div className="pointer-events-auto">
                <ButtonWithIcon
                  text={saving ? 'Saving…' : 'Save Preferences'}
                  icon={<Save size={16} strokeWidth={2} aria-hidden="true" />}
                  isSecondary
                  size="small"
                  aria-label="Save Preferences"
                  disabled={loading || saving || !hasUnsavedChanges}
                  onClick={() => void handleSave()}
                />
              </div>
            </div>
          </div>
        </SheetContent>
      </Sheet>
      <CborgModelPickerDialog
        open={cborgPickerOpen}
        onOpenChange={setCborgPickerOpen}
        value={draftSettings.model}
        options={availableCborgModels}
        onChange={updateModel}
      />
    </>
  );
}
