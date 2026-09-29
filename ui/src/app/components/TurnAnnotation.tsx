import { useState } from 'react';
import type { TurnAnnotationState } from './chatSessions';
import { postTurnAnnotation } from './uiTelemetry';

export function TurnAnnotationBar({
  turnId,
  annotation,
  onSaved,
}: {
  turnId?: string;
  annotation?: TurnAnnotationState | null;
  onSaved: (annotation: TurnAnnotationState) => void;
}) {
  const [note, setNote] = useState(annotation?.note ?? '');
  const [tags, setTags] = useState<string[]>(annotation?.failureTags ?? []);
  const [tagDraft, setTagDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  function addTag() {
    const next = tagDraft.trim();
    if (!next || tags.includes(next)) {
      setTagDraft('');
      return;
    }
    setTags([...tags, next]);
    setTagDraft('');
  }

  async function submit(verdict: 'pass' | 'fail') {
    if (!turnId || saving) return;
    setSaving(true);
    setError('');
    const ok = await postTurnAnnotation({
      turnId,
      verdict,
      failureTags: tags,
      note: note.trim(),
    });
    setSaving(false);
    if (!ok) {
      setError('Could not save the label.');
      return;
    }
    onSaved({
      verdict,
      failureTags: tags,
      note: note.trim(),
      saved: true,
    });
  }

  const saved = annotation?.saved ? annotation : null;

  return (
    <div className="mt-2 space-y-2 rounded-lg border border-slate-200 bg-white px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">Label</span>
        <button
          type="button"
          disabled={!turnId || saving}
          onClick={() => void submit('pass')}
          className={`rounded-md px-2.5 py-1 text-xs font-medium ${
            saved?.verdict === 'pass'
              ? 'bg-emerald-600 text-white'
              : 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
          } disabled:opacity-40`}
        >
          Pass
        </button>
        <button
          type="button"
          disabled={!turnId || saving}
          onClick={() => void submit('fail')}
          className={`rounded-md px-2.5 py-1 text-xs font-medium ${
            saved?.verdict === 'fail'
              ? 'bg-rose-600 text-white'
              : 'bg-rose-50 text-rose-700 hover:bg-rose-100'
          } disabled:opacity-40`}
        >
          Fail
        </button>
        {saved && (
          <span className="text-[11px] text-slate-500">
            Saved {saved.verdict}
          </span>
        )}
      </div>
      <label className="block text-[11px] text-slate-500">
        Note
        <input
          value={note}
          onChange={event => setNote(event.target.value)}
          className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-700"
          placeholder="Optional note"
        />
      </label>
      <div className="flex flex-wrap items-center gap-1.5">
        {tags.map(tag => (
          <button
            key={tag}
            type="button"
            onClick={() => setTags(tags.filter(item => item !== tag))}
            className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600"
            title="Remove tag"
          >
            {tag} ×
          </button>
        ))}
        <input
          value={tagDraft}
          onChange={event => setTagDraft(event.target.value)}
          onKeyDown={event => {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            addTag();
          }}
          className="min-w-24 flex-1 rounded-md border border-slate-200 px-2 py-1 text-xs text-slate-700"
          placeholder="Add a tag"
        />
        <button
          type="button"
          onClick={addTag}
          className="rounded-md border border-slate-200 px-2 py-1 text-[11px] text-slate-600"
        >
          Add tag
        </button>
      </div>
      {error && <p className="text-[11px] text-rose-600">{error}</p>}
    </div>
  );
}
