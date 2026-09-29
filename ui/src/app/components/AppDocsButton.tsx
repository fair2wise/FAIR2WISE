import { useState } from 'react';
import { ButtonWithIcon } from '@blueskyproject/finch';
import { BookOpen } from 'lucide-react';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from './ui/sheet';

/** Same-origin MkDocs site served by Vite at /docs/. Override with VITE_F2W_DOCS_URL. */
const MKDOCS_URL = (import.meta.env.VITE_F2W_DOCS_URL || '/docs/').replace(/\/?$/, '/');

export function AppDocsButton() {
  const [open, setOpen] = useState(false);
  const [loadError, setLoadError] = useState('');

  return (
    <>
      <ButtonWithIcon
        text="Docs"
        icon={<BookOpen size={16} strokeWidth={2} aria-hidden="true" />}
        isSecondary
        size="small"
        aria-label="Documentation"
        onClick={() => {
          setLoadError('');
          setOpen(true);
        }}
      />
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent
          side="right"
          className="flex h-full w-full flex-col gap-0 overflow-hidden bg-white p-0 text-slate-800 sm:max-w-[80vw]"
        >
          <SheetHeader className="shrink-0 border-b border-slate-200">
            <SheetTitle>Documentation</SheetTitle>
            <SheetDescription>FAIR2WISE project docs</SheetDescription>
          </SheetHeader>
          <div className="relative min-h-0 flex-1">
            {loadError && (
              <div className="absolute inset-x-0 top-0 z-10 border-b border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
                {loadError}
                {' '}
                <a
                  href={MKDOCS_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium underline"
                >
                  Open in a new tab
                </a>
              </div>
            )}
            <iframe
              src={open ? MKDOCS_URL : 'about:blank'}
              title="FAIR2WISE Documentation"
              className="absolute inset-0 h-full w-full border-none"
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
              onLoad={event => {
                try {
                  const doc = event.currentTarget.contentDocument;
                  const text = doc?.body?.innerText || '';
                  if (!doc || text.includes('MkDocs site is not built yet')) {
                    setLoadError('Documentation is not available in this session.');
                    return;
                  }
                  setLoadError('');
                } catch {
                  setLoadError('Documentation failed to load.');
                }
              }}
            />
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
