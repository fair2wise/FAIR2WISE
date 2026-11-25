import { FileCode2 } from 'lucide-react';
import { useState } from 'react';
import { useCapabilities } from '../capabilitiesHooks';
import { Button } from '../ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../ui/dialog';
import { SchemaPanel } from './SchemaPanel';
import { useUserSchemas } from './schemaHooks';

/** Props of {@link SchemaPanelButton}. */
export interface SchemaPanelButtonProps {
  /** `<select>` value of the model the calling form has chosen. */
  modelValue?: string;
  /** A version was saved: the id to select in the caller's chooser. */
  onSaved?: (id: string) => void;
}

/**
 * Whether the schema panel may show. The routes are local-only and need `library.author_schema`, which
 * `GET /v1/capabilities` does not report; the probe `GET /v1/library/schemas/user` answers 404 on ALS and 403 without
 * the capability, so only a real list shows the button. A capabilities answer that names an ALS role hides it too.
 */
export function useSchemaPanelAvailable(): boolean {
  const probe = useUserSchemas();
  const caps = useCapabilities();
  if (probe.data === undefined || probe.data === null) return false;
  return caps?.role === undefined || caps.role === null || caps.role === 'local_operator';
}

/**
 * "Create or edit a schema" button that opens the schema panel in a dialog. Renders nothing on a server or for a
 * caller without the schema routes (ALS, no capability).
 */
export function SchemaPanelButton({ modelValue, onSaved }: SchemaPanelButtonProps) {
  const available = useSchemaPanelAvailable();
  const [open, setOpen] = useState(false);
  if (!available) return null;
  return (
    <>
      <Button type="button" variant="outline" size="sm" onClick={() => setOpen(true)} data-testid="open-schema-panel">
        <FileCode2 aria-hidden="true" />
        Create or edit a schema
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[92vh] overflow-y-auto bg-background text-foreground sm:max-w-4xl" data-testid="schema-dialog">
          <DialogHeader>
            <DialogTitle>Schema</DialogTitle>
            <DialogDescription>
              Draft a LinkML schema with a model, or paste, upload or edit one. A schema is checked before it can be saved; a saved
              version is immutable and appears in the schema chooser as a user schema. User schemas cannot be published yet.
            </DialogDescription>
          </DialogHeader>
          <SchemaPanel modelValue={modelValue} onSaved={onSaved} />
        </DialogContent>
      </Dialog>
    </>
  );
}
