import { useEffect, useState } from 'react';
import * as RadioGroupPrimitive from '@radix-ui/react-radio-group';
import { ButtonWithIcon } from '@blueskyproject/finch';
import { Save, Settings, Info } from 'lucide-react';
import {
  DEFAULT_AGENT_SETTINGS,
  loadAgentSettings,
  saveAgentSettings,
  settingsEqual,
  settingsFromApiResponse,
  settingsToApiPayload,
  type AgentBackend,
  type AgentGraphSource,
  type AgentSettings,
} from './agentSettings';
import { AppErrorMessage } from './AppErrorMessage';
import { AsciiOrb } from './AsciiOrb';
import { fetchAgentSettings, updateAgentSettings } from './data/liveAgent';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
import { Label } from './ui/label';
import { RadioGroup } from './ui/radio-group';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from './ui/select';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from './ui/sheet';

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
          <AsciiOrb size={16} className="text-black" interactive={false} />
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
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const hasUnsavedChanges = !settingsEqual(draftSettings, savedSettings);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError('');
      const saved = loadAgentSettings();
      setSavedSettings(saved);
      setDraftSettings(saved);
      try {
        const response = await fetchAgentSettings();
        if (cancelled) return;
        setAvailableJsonGraphs(response.available_json_graphs ?? []);
        const synced = settingsFromApiResponse(response);
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
    try {
      const response = await updateAgentSettings(settingsToApiPayload(draftSettings));
      const synced = settingsFromApiResponse(response);
      const saved: AgentSettings = {
        backend: draftSettings.backend,
        graphSource: draftSettings.graphSource,
        jsonGraphPath: draftSettings.graphSource === 'json'
          ? draftSettings.jsonGraphPath
          : synced.jsonGraphPath,
      };
      setSavedSettings(saved);
      setDraftSettings(saved);
      saveAgentSettings(saved);
      setAvailableJsonGraphs(response.available_json_graphs ?? []);
      await onSettingsApplied?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  function updateBackend(backend: AgentBackend) {
    setDraftSettings(prev => ({ ...prev, backend }));
  }

  function updateGraphSource(graphSource: AgentGraphSource) {
    const jsonGraphPath = draftSettings.jsonGraphPath
      || availableJsonGraphs[0]
      || DEFAULT_AGENT_SETTINGS.jsonGraphPath;
    setDraftSettings(prev => ({ ...prev, graphSource, jsonGraphPath }));
  }

  function updateJsonGraphPath(jsonGraphPath: string) {
    setDraftSettings(prev => ({ ...prev, graphSource: 'json', jsonGraphPath }));
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
                  </AlertDescription>
                </Alert>

                <div className="space-y-2">
                <Label htmlFor="json-graph-select" className="text-xs font-medium text-slate-500">
                  JSON graph file
                </Label>
                {availableJsonGraphs.length > 0 ? (
                  <Select
                    value={draftSettings.jsonGraphPath}
                    onValueChange={updateJsonGraphPath}
                    disabled={loading || saving}
                  >
                    <SelectTrigger id="json-graph-select" className="w-full bg-white">
                      <SelectValue placeholder="Select a JSON graph" />
                    </SelectTrigger>
                    <SelectContent>
                      {availableJsonGraphs.map(path => (
                        <SelectItem key={path} value={path}>
                          {path.replace(/^storage\/kg\//, '')}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                    {loading ? 'Loading available JSON graphs…' : 'No JSON graph files found in storage/kg.'}
                  </div>
                )}
                </div>
              </div>
            )}

            {error && (
              <AppErrorMessage title="Settings update failed" className="text-xs">
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
    </>
  );
}
