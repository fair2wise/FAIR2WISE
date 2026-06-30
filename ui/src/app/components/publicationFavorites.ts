import { useCallback, useEffect, useState } from 'react';
import type { PublicationInfo } from './data/liveAgent';

const STORAGE_KEY = 'fair2wise-publication-favorites-v1';
const FAVORITES_CHANGED = 'fair2wise-favorites-changed';

export interface StoredFavorite {
  key: string;
  savedAt: number;
  publication: PublicationInfo;
}

/** Stable id aligned with backend `_publication_key` in f2w_agent/api.py */
export function publicationKey(publication: PublicationInfo): string {
  const doi = (publication.doi ?? '').trim().toLowerCase();
  if (doi) return `doi:${doi}`;
  const source = (publication.source_paper ?? '').trim().toLowerCase();
  const title = (publication.paper_title ?? publication.source_paper ?? '').trim().toLowerCase();
  return `source-title:${source}:${title}`;
}

function readStoredFavorites(): StoredFavorite[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is StoredFavorite =>
        Boolean(item)
        && typeof item === 'object'
        && typeof item.key === 'string'
        && typeof item.savedAt === 'number'
        && typeof item.publication === 'object',
    );
  } catch {
    return [];
  }
}

function writeStoredFavorites(favorites: StoredFavorite[]): void {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(favorites));
}

function emitFavoritesChanged(): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(FAVORITES_CHANGED));
}

export function readBookmarkedPublications(): StoredFavorite[] {
  return readStoredFavorites();
}

export function readFavoriteKeys(): Set<string> {
  return new Set(readStoredFavorites().map(item => item.key));
}

export function isPublicationFavorite(
  publication: PublicationInfo,
  favoriteKeys: Set<string>,
): boolean {
  return favoriteKeys.has(publicationKey(publication));
}

export function togglePublicationFavorite(publication: PublicationInfo): boolean {
  const key = publicationKey(publication);
  const favorites = readStoredFavorites();
  const index = favorites.findIndex(item => item.key === key);
  if (index >= 0) {
    favorites.splice(index, 1);
    writeStoredFavorites(favorites);
    emitFavoritesChanged();
    return false;
  }
  favorites.unshift({
    key,
    savedAt: Date.now(),
    publication,
  });
  writeStoredFavorites(favorites);
  emitFavoritesChanged();
  return true;
}

export function usePublicationFavorites() {
  const [favoriteKeys, setFavoriteKeys] = useState<Set<string>>(() => readFavoriteKeys());
  const [bookmarks, setBookmarks] = useState<StoredFavorite[]>(() => readStoredFavorites());

  useEffect(() => {
    const sync = () => {
      setFavoriteKeys(readFavoriteKeys());
      setBookmarks(readStoredFavorites());
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) sync();
    };
    window.addEventListener(FAVORITES_CHANGED, sync);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener(FAVORITES_CHANGED, sync);
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  const isFavorite = useCallback(
    (publication: PublicationInfo) => isPublicationFavorite(publication, favoriteKeys),
    [favoriteKeys],
  );

  const toggle = useCallback((publication: PublicationInfo) => {
    const saved = togglePublicationFavorite(publication);
    setFavoriteKeys(readFavoriteKeys());
    setBookmarks(readStoredFavorites());
    return saved;
  }, []);

  return { favoriteKeys, bookmarks, isFavorite, toggle };
}
