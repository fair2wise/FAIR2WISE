import { useMemo, useState } from 'react';
import { ButtonWithIcon } from '@blueskyproject/finch';
import { Search, Trash2 } from 'lucide-react';
import type { ChatSession } from './chatSessions';
import { matchingChatSessions } from './chatSessions';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from './ui/sheet';
import { cn } from './ui/utils';

function formatUpdatedAt(value: number): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value));
}

export function AppSearchChatsButton({
  sessions,
  activeSessionId,
  onSelect,
  onDelete,
}: {
  sessions: ChatSession[];
  activeSessionId: string;
  onSelect: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const filteredSessions = useMemo(
    () => matchingChatSessions(sessions, query),
    [sessions, query],
  );

  function handleOpenChange(nextOpen: boolean) {
    setOpen(nextOpen);
    if (!nextOpen) setQuery('');
  }

  function handleSelect(sessionId: string) {
    onSelect(sessionId);
    handleOpenChange(false);
  }

  return (
    <>
      <ButtonWithIcon
        text="Search chats"
        icon={<Search size={16} strokeWidth={2} aria-hidden="true" />}
        isSecondary
        size="small"
        aria-label="Search chats"
        onClick={() => setOpen(true)}
      />
      <Sheet open={open} onOpenChange={handleOpenChange}>
        <SheetContent
          side="right"
          className="flex w-full flex-col gap-0 bg-white p-0 text-slate-800 sm:max-w-xl"
        >
          <SheetHeader className="border-b border-slate-200">
            <SheetTitle>Search chats</SheetTitle>
            <SheetDescription>
              Select a previous chat session.
            </SheetDescription>
          </SheetHeader>
          <div className="relative min-h-0 flex-1">
            <div className="border-b border-slate-200 bg-white px-4 py-4">
              <label className="flex h-9 items-center gap-2 rounded-md border border-slate-200 bg-white px-3 shadow-sm focus-within:border-sky-400">
                <Search size={15} className="shrink-0 text-slate-400" aria-hidden="true" />
                <input
                  value={query}
                  onChange={event => setQuery(event.target.value)}
                  placeholder="Search chat titles"
                  aria-label="Search chat titles"
                  autoFocus
                  className="min-w-0 flex-1 bg-transparent text-sm text-slate-800 outline-none placeholder:text-slate-400"
                />
              </label>
            </div>
            <div className="h-full space-y-3 overflow-y-auto px-4 py-4 pb-24 text-sm">
              {filteredSessions.map(session => {
                const selected = session.id === activeSessionId;
                return (
                  <div
                    key={session.id}
                    className={cn(
                      'flex w-full items-center rounded-lg text-left text-sm transition hover:bg-slate-50',
                      selected && 'bg-sky-50 ring-1 ring-sky-200',
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => handleSelect(session.id)}
                      className="flex min-w-0 flex-1 items-center gap-3 px-4 py-3.5 text-left"
                    >
                      <span
                        className={cn(
                          'flex size-4 shrink-0 items-center justify-center rounded-full border border-slate-300',
                          selected && 'border-sky-500 bg-sky-500',
                        )}
                        aria-hidden="true"
                      />
                      <span className="min-w-0">
                        <span className="block truncate font-medium text-slate-800">{session.title}</span>
                        <span className="mt-1 block truncate text-xs text-slate-500">
                          Updated {formatUpdatedAt(session.updatedAt)}
                        </span>
                      </span>
                    </button>
                    <button
                      type="button"
                      aria-label={`Delete ${session.title}`}
                      title="Delete chat"
                      onClick={() => onDelete(session.id)}
                      className="mr-3 inline-flex size-8 shrink-0 items-center justify-center rounded-md text-slate-400 transition hover:bg-rose-50 hover:text-rose-600"
                    >
                      <Trash2 size={16} aria-hidden="true" />
                    </button>
                  </div>
                );
              })}
              {filteredSessions.length === 0 && (
                <p className="px-4 py-8 text-center text-sm text-slate-500">
                  No chats match your search.
                </p>
              )}
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
