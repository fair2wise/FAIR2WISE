import { useState } from 'react';
import { ButtonWithIcon } from '@blueskyproject/finch';
import { Bookmark } from 'lucide-react';
import { PublicationList } from './PublicationList';
import { usePublicationFavorites } from './publicationFavorites';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from './ui/sheet';

export function AppBookmarksButton() {
  const [open, setOpen] = useState(false);
  const { bookmarks } = usePublicationFavorites();
  const publications = bookmarks.map(item => item.publication);

  return (
    <>
      <ButtonWithIcon
        text="Bookmarks"
        icon={<Bookmark size={16} strokeWidth={2} aria-hidden="true" />}
        isSecondary
        size="small"
        aria-label="Bookmarks"
        onClick={() => setOpen(true)}
      />
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="w-full bg-white text-slate-800 sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>Bookmarks</SheetTitle>
            <SheetDescription>
              Publications saved from chat, paper search, and the knowledge graph
            </SheetDescription>
          </SheetHeader>
          <div className="flex min-h-0 flex-1 flex-col gap-4 px-4 pb-4">
            {publications.length === 0 ? (
              <div className="rounded-md border border-slate-200 bg-slate-50 px-3 py-3 text-xs leading-relaxed text-slate-600">
                No bookmarked publications yet. Use the bookmark icon next to a publication to save it here.
              </div>
            ) : (
              <div className="min-h-0 flex-1 overflow-y-auto border-t border-slate-200 pt-1">
                <div className="mb-1 text-xs text-slate-500">
                  {publications.length} bookmarked publication{publications.length === 1 ? '' : 's'}
                </div>
                <PublicationList publications={publications} intro={null} />
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
