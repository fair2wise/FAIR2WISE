import { useId } from 'react';
import { folderLabel, isValidFolderName, type CreatedCategory, type LibraryFolder } from '../libraryApi';
import { Label } from '../ui/label';
import { NewCategoryField } from './NewCategoryField';

/** `value` of the "new category" option of the select. */
export const NEW_CATEGORY = '\u0000new';

/** The folder an upload goes to: `''` is the library root, a name is an existing or new category. */
export interface UploadTargetValue {
  /** Select value: `''` (root), a folder name, or {@link NEW_CATEGORY}. */
  choice: string;
  /** Name typed for a new category (used when `choice` is {@link NEW_CATEGORY}). */
  newName: string;
}

/** The folder to send with an upload, or `null` while a new category's name is missing or invalid. */
export function resolveUploadFolder(target: UploadTargetValue): string | null {
  if (target.choice !== NEW_CATEGORY) return target.choice;
  const name = target.newName.trim();
  return isValidFolderName(name) ? name : null;
}

/** Props of {@link UploadTarget}. */
export interface UploadTargetProps {
  /** Existing categories (from `paperFolders`). */
  folders: LibraryFolder[];
  value: UploadTargetValue;
  onChange: (next: UploadTargetValue) => void;
  /** Creates a category on the server (the shared list). */
  onCreateCategory: (name: string) => Promise<CreatedCategory>;
  disabled?: boolean;
}

const SELECT_CLASS =
  'h-9 min-w-0 rounded-md border border-border bg-background px-3 text-sm shadow-sm outline-none '
  + 'focus:border-ring disabled:cursor-not-allowed disabled:opacity-50';

/**
 * "Upload to" control: the library root, an existing category, or a new category by name. Inside a category the
 * server files each paper under its publication year and records it in the category's manifest.
 */
export function UploadTarget({ folders, value, onChange, onCreateCategory, disabled = false }: UploadTargetProps) {
  const selectId = useId();
  const helpId = useId();
  const creating = value.choice === NEW_CATEGORY;
  const invalid = creating && value.newName.trim() !== '' && !isValidFolderName(value.newName.trim());
  const named = folders.filter(folder => folder.name !== '');
  return (
    <div className="flex flex-wrap items-end gap-3">
      <div className="space-y-1">
        <Label htmlFor={selectId}>Upload to</Label>
        <select
          id={selectId}
          value={value.choice}
          disabled={disabled}
          onChange={event => onChange({ ...value, choice: event.target.value })}
          className={SELECT_CLASS}
        >
          <option value="">{folderLabel('')} (library root)</option>
          {named.map(folder => (
            <option key={folder.name} value={folder.name}>{folder.name}</option>
          ))}
          <option value={NEW_CATEGORY}>New category…</option>
        </select>
      </div>
      {creating && (
        <NewCategoryField
          value={value.newName}
          onChange={newName => onChange({ ...value, newName })}
          existing={named.map(folder => folder.name)}
          onCreate={onCreateCategory}
          onCreated={name => onChange({ choice: name, newName: '' })}
          disabled={disabled}
        />
      )}
      <p id={helpId} className={invalid ? 'text-xs text-destructive' : 'text-xs text-muted-foreground'}>
        {invalid
          ? 'Letters, digits, "_" and "-"; start with a letter or digit; up to 64 characters.'
          : value.choice === ''
            ? 'Files in the root are not filed by year.'
            : 'Filed under the paper’s publication year (unknown if it cannot be read) and added to the category manifest.'}
      </p>
    </div>
  );
}
