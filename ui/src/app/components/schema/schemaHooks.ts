/** TanStack Query hooks and the debounced validator of the schema panel. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { extractionKeys } from '../extractionHooks';
import {
  MAX_SCHEMA_BYTES,
  deleteSchemaVersion,
  probeUserSchemas,
  saveSchema,
  utf8Length,
  validateSchema,
  type SchemaReport,
} from './schemaApi';

/** Query key of the user schema list. */
export const userSchemaKeys = { list: ['schemas', 'user'] as const };

/**
 * The user schemas, `null` when this server or caller has no schema routes (ALS answers 404, a caller without the
 * capability 403), `undefined` while loading. The panel's entry points show only on an array.
 */
export function useUserSchemas(enabled = true) {
  return useQuery({
    queryKey: userSchemaKeys.list,
    queryFn: () => probeUserSchemas(),
    enabled,
    retry: false,
    staleTime: 30_000,
  });
}

/** Refresh everything that lists schemas: the panel's list and the chooser of the extraction form. */
export function useRefreshSchemaLists() {
  const client = useQueryClient();
  return useCallback(async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: userSchemaKeys.list }),
      client.invalidateQueries({ queryKey: extractionKeys.schemas }),
    ]);
  }, [client]);
}

/** Save as a new version; the lists are refreshed on success so the chooser shows it. */
export function useSaveSchema() {
  const refresh = useRefreshSchemaLists();
  return useMutation({
    mutationFn: (input: { name: string; text: string }) => saveSchema(input.name, input.text),
    onSuccess: () => refresh(),
  });
}

/** Delete one version (409 `schema_in_use` is shown by the caller). */
export function useDeleteSchemaVersion() {
  const refresh = useRefreshSchemaLists();
  return useMutation({
    mutationFn: (id: string) => deleteSchemaVersion(id),
    onSuccess: () => refresh(),
  });
}

/** Debounce of the continuous validation (milliseconds). */
export const VALIDATE_DEBOUNCE_MS = 500;

/** State of {@link useSchemaValidation}. */
export interface SchemaValidation {
  /** `idle`: nothing to check; `waiting`: typing, request not sent yet; `checking`: request in flight. */
  status: 'idle' | 'waiting' | 'checking' | 'done' | 'error';
  /** The last report received. */
  report: SchemaReport | null;
  /** The exact text `report` was computed for (the report says nothing about any other text). */
  checkedText: string | null;
  error: string | null;
  /** Check now, without waiting for the debounce. */
  recheck: () => void;
}

/**
 * Validate `text` on the server (V89) about 500 ms after the last change. A newer change cancels the request in
 * flight (AbortController) and its answer is ignored, so a slow old answer never overwrites a newer one. Text over
 * the server cap is not sent. Nothing is logged or stored.
 */
export function useSchemaValidation(text: string, enabled = true, debounceMs = VALIDATE_DEBOUNCE_MS): SchemaValidation {
  const [state, setState] = useState<Omit<SchemaValidation, 'recheck'>>({
    status: 'idle', report: null, checkedText: null, error: null,
  });
  const controller = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const textRef = useRef(text);
  textRef.current = text;

  const run = useCallback((value: string) => {
    controller.current?.abort();
    const mine = new AbortController();
    controller.current = mine;
    setState(prev => ({ ...prev, status: 'checking', error: null }));
    validateSchema(value, mine.signal)
      .then(report => {
        if (mine.signal.aborted) return;
        setState({ status: 'done', report, checkedText: value, error: null });
      })
      .catch((error: unknown) => {
        if (mine.signal.aborted) return;
        setState(prev => ({ ...prev, status: 'error', error: error instanceof Error ? error.message : 'The check failed.' }));
      });
  }, []);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    controller.current?.abort();
    if (!enabled || text.trim() === '' || utf8Length(text) > MAX_SCHEMA_BYTES) {
      setState({ status: 'idle', report: null, checkedText: null, error: null });
      return undefined;
    }
    setState(prev => ({ ...prev, status: 'waiting', error: null }));
    timer.current = setTimeout(() => run(text), debounceMs);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [text, enabled, debounceMs, run]);

  useEffect(() => () => controller.current?.abort(), []);

  const recheck = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    if (enabled && textRef.current.trim() !== '' && utf8Length(textRef.current) <= MAX_SCHEMA_BYTES) run(textRef.current);
  }, [enabled, run]);

  return { ...state, recheck };
}
