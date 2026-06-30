import { Bookmark } from 'lucide-react';
import type { PublicationInfo } from './data/liveAgent';
import { usePublicationFavorites } from './publicationFavorites';

export function PublicationFavoriteButton({ publication }: { publication: PublicationInfo }) {
  const { isFavorite, toggle } = usePublicationFavorites();
  const favorited = isFavorite(publication);

  return (
    <button
      type="button"
      aria-label={favorited ? 'Remove bookmark' : 'Bookmark publication'}
      aria-pressed={favorited}
      title={favorited ? 'Bookmarked' : 'Bookmark publication'}
      onClick={() => toggle(publication)}
      className={`inline-flex h-7 w-7 items-center justify-center rounded-md transition hover:bg-slate-100 ${
        favorited ? 'text-sky-600 hover:text-sky-700' : 'text-slate-400 hover:text-slate-700'
      }`}
    >
      <Bookmark size={14} fill={favorited ? 'currentColor' : 'none'} aria-hidden="true" />
    </button>
  );
}
