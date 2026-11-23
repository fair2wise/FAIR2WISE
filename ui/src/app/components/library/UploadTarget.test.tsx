import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { NEW_CATEGORY, resolveUploadFolder, UploadTarget, type UploadTargetValue } from './UploadTarget';

const FOLDERS = [{ name: 'rsoxs', count: 2 }, { name: 'tomo', count: 1 }, { name: '', count: 1 }];

const created = (name: string) => Promise.resolve({ name, created: true, count: 0 });

function Harness({ disabled = false, initial = { choice: '', newName: '' }, onCreate = created }: {
  disabled?: boolean;
  initial?: UploadTargetValue;
  onCreate?: (name: string) => Promise<{ name: string; created: boolean; count: number }>;
}) {
  const [value, setValue] = useState(initial);
  const [folders, setFolders] = useState(FOLDERS);
  return (
    <>
      <UploadTarget
        folders={folders}
        value={value}
        onChange={setValue}
        onCreateCategory={async name => {
          const made = await onCreate(name); // the shared list gains the name, as the real hook does
          setFolders(current => [...current, { name: made.name, count: 0 }]);
          return made;
        }}
        disabled={disabled}
      />
      <output data-testid="resolved">{String(resolveUploadFolder(value))}</output>
    </>
  );
}

describe('resolveUploadFolder', () => {
  it('is the chosen folder, the trimmed new name, or null for an invalid new name', () => {
    expect(resolveUploadFolder({ choice: '', newName: '' })).toBe('');
    expect(resolveUploadFolder({ choice: 'tomo', newName: 'ignored' })).toBe('tomo');
    expect(resolveUploadFolder({ choice: NEW_CATEGORY, newName: ' xray ' })).toBe('xray');
    expect(resolveUploadFolder({ choice: NEW_CATEGORY, newName: '' })).toBeNull();
    expect(resolveUploadFolder({ choice: NEW_CATEGORY, newName: 'a/b' })).toBeNull();
  });
});

describe('UploadTarget', () => {
  it('offers the root, each category (not the root twice) and a new category', () => {
    render(<Harness />);
    const options = screen.getAllByRole('option').map(option => option.textContent);
    expect(options).toEqual(['Uncategorized (library root)', 'rsoxs', 'tomo', 'New category…']);
    expect(screen.getByLabelText('Upload to')).toHaveValue('');
  });

  it('selects an existing category', async () => {
    render(<Harness />);
    await userEvent.selectOptions(screen.getByLabelText('Upload to'), 'tomo');
    expect(screen.getByTestId('resolved')).toHaveTextContent('tomo');
    expect(screen.queryByLabelText('New category name')).not.toBeInTheDocument();
  });

  it('asks for a name for a new category and flags an invalid one', async () => {
    render(<Harness />);
    await userEvent.selectOptions(screen.getByLabelText('Upload to'), 'New category…');
    const name = screen.getByLabelText('New category name');
    expect(screen.getByTestId('resolved')).toHaveTextContent('null');
    await userEvent.type(name, 'bad name');
    expect(name).toHaveAttribute('aria-invalid', 'true');
    await userEvent.clear(name);
    await userEvent.type(name, 'xray');
    expect(name).toHaveAttribute('aria-invalid', 'false');
    expect(screen.getByTestId('resolved')).toHaveTextContent('xray');
  });

  it('disables the controls when disabled', () => {
    render(<Harness disabled initial={{ choice: NEW_CATEGORY, newName: '' }} />);
    expect(screen.getByLabelText('Upload to')).toBeDisabled();
    expect(screen.getByLabelText('New category name')).toBeDisabled();
  });

  it('Enter in the new-category field creates it and selects it', async () => {
    const onCreate = vi.fn(created);
    render(<Harness onCreate={onCreate} />);
    await userEvent.selectOptions(screen.getByLabelText('Upload to'), 'New category…');
    await userEvent.type(screen.getByLabelText('New category name'), '  xray {Enter}');
    expect(onCreate).toHaveBeenCalledWith('xray');
    await waitFor(() => expect(screen.getByLabelText('Upload to')).toHaveValue('xray'));
    expect(screen.queryByLabelText('New category name')).not.toBeInTheDocument();
    expect(screen.getByTestId('resolved')).toHaveTextContent('xray');
  });

  it('the visible Add category button does the same and is disabled for an invalid name', async () => {
    const onCreate = vi.fn(created);
    render(<Harness onCreate={onCreate} />);
    await userEvent.selectOptions(screen.getByLabelText('Upload to'), 'New category…');
    const add = screen.getByRole('button', { name: 'Add category' });
    expect(add).toBeDisabled();
    await userEvent.type(screen.getByLabelText('New category name'), '../x');
    expect(add).toBeDisabled();
    expect(onCreate).not.toHaveBeenCalled();
    await userEvent.clear(screen.getByLabelText('New category name'));
    await userEvent.type(screen.getByLabelText('New category name'), 'xray');
    await userEvent.click(add);
    await waitFor(() => expect(screen.getByLabelText('Upload to')).toHaveValue('xray'));
  });

  it('a duplicate name (any case) selects the existing category without a request', async () => {
    const onCreate = vi.fn(created);
    render(<Harness onCreate={onCreate} />);
    await userEvent.selectOptions(screen.getByLabelText('Upload to'), 'New category…');
    await userEvent.type(screen.getByLabelText('New category name'), 'TOMO{Enter}');
    await waitFor(() => expect(screen.getByLabelText('Upload to')).toHaveValue('tomo'));
    expect(onCreate).not.toHaveBeenCalled();
  });

  it('shows the server error and keeps the field', async () => {
    const onCreate = vi.fn(() => Promise.reject(new Error('that name is taken by something that is not a folder')));
    render(<Harness onCreate={onCreate} />);
    await userEvent.selectOptions(screen.getByLabelText('Upload to'), 'New category…');
    await userEvent.type(screen.getByLabelText('New category name'), 'taken{Enter}');
    expect(await screen.findByRole('alert')).toHaveTextContent('not a folder');
    expect(screen.getByLabelText('New category name')).toHaveValue('taken');
    expect(screen.getByLabelText('New category name')).toHaveAttribute('aria-invalid', 'true');
  });
});
