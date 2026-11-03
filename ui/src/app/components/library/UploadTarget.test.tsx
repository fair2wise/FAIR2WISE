import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { NEW_CATEGORY, resolveUploadFolder, UploadTarget, type UploadTargetValue } from './UploadTarget';

const FOLDERS = [{ name: 'rsoxs', count: 2 }, { name: 'tomo', count: 1 }, { name: '', count: 1 }];

function Harness({ disabled = false, initial = { choice: '', newName: '' } }: { disabled?: boolean; initial?: UploadTargetValue }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <UploadTarget folders={FOLDERS} value={value} onChange={setValue} disabled={disabled} />
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
});
