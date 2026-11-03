import { folderLabel, type LibraryFolder } from '../libraryApi';

/** Props of {@link FolderFilter}. */
export interface FolderFilterProps {
  /** Folders to offer (from `paperFolders`). */
  folders: LibraryFolder[];
  /** Total number of papers (the count shown on the "All" option). */
  total: number;
  /** Selected folder name, `''` for the root, or `null` for all folders. */
  value: string | null;
  onChange: (next: string | null) => void;
  /** Accessible name of the group; defaults to "Folder". */
  label?: string;
  disabled?: boolean;
}

/**
 * Single-selection folder (category) filter: "All" plus one option per top-level folder with its paper
 * count. Rendered as a radiogroup so keyboard and screen-reader users get one tab stop and `aria-checked`.
 */
export function FolderFilter({ folders, total, value, onChange, label = 'Folder', disabled = false }: FolderFilterProps) {
  const options: { key: string | null; text: string; count: number }[] = [
    { key: null, text: 'All', count: total },
    ...folders.map(folder => ({ key: folder.name, text: folderLabel(folder.name), count: folder.count })),
  ];
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-2">
      {options.map(option => {
        const checked = option.key === value;
        return (
          <button
            key={option.key ?? '__all__'}
            type="button"
            role="radio"
            aria-checked={checked}
            disabled={disabled}
            onClick={() => onChange(option.key)}
            className={`rounded-full border px-3 py-1 text-sm disabled:cursor-not-allowed disabled:opacity-50 ${
              checked
                ? 'border-primary bg-primary text-primary-foreground'
                : 'border-border bg-background text-foreground hover:bg-accent'
            }`}
          >
            {option.text} <span className={checked ? 'opacity-80' : 'text-muted-foreground'}>({option.count})</span>
          </button>
        );
      })}
    </div>
  );
}
