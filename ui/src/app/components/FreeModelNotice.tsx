/**
 * Notice shown under a CBORG model picker when the saved or drafted id is not one of the free
 * LBL-hosted models the server offers (owner decision 2026-11-09: only free models via CBORG).
 *
 * The server never uses a refused id: `PUT /settings` and the chat runtime replace it with the
 * default free model, and the judge route refuses it. This component only makes that visible;
 * it is additive and renders nothing while the picked id is allowed or no list is known yet.
 */
import { freeModelFallback } from './agentSettings';

export interface FreeModelNoticeProps {
  /** The id the user picked or had saved. */
  model: string;
  /** The ids the server currently offers (already filtered to free models). */
  allowed: string[];
  /** The free id the server falls back to. */
  fallback: string;
  /** What the picker is for, e.g. "chat model" or "judge model". */
  role?: string;
  /** The server already said the id is refused (judge route): show the notice even without a list. */
  refused?: boolean;
}

export function FreeModelNotice({ model, allowed, fallback, role = 'model', refused }: FreeModelNoticeProps) {
  const replaced = refused ?? freeModelFallback(model, allowed, fallback).replaced;
  if (!replaced) return null;
  return (
    <p
      role="status"
      data-testid="free-model-notice"
      className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
    >
      <span className="font-medium">{model}</span> is not one of the free LBL-hosted CBORG models, so it is not used
      {` as the ${role}`}: the default free model <span className="font-medium">{fallback}</span> is used instead.
      Only free models are allowed via CBORG (owner decision 2026-11-09); pick one from the list.
    </p>
  );
}
