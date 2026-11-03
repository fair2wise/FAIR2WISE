import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { paperFolders, type LibraryPaper } from '../libraryApi';
import { FolderFilter } from './FolderFilter';

const at = (folder: string): LibraryPaper =>
  ({ id: folder + Math.random(), folder, relpath: `${folder}/x.pdf`, file_name: 'x.pdf' }) as LibraryPaper;

describe('paperFolders', () => {
  it('counts per top-level folder, sorted, with the root last', () => {
    const folders = paperFolders([at('tomo'), at(''), at('rsoxs'), at('rsoxs')]);
    expect(folders).toEqual([
      { name: 'rsoxs', count: 2 },
      { name: 'tomo', count: 1 },
      { name: '', count: 1 },
    ]);
  });
});

describe('FolderFilter', () => {
  const folders = [{ name: 'rsoxs', count: 2 }, { name: '', count: 1 }];

  it('is a labelled radiogroup with the selected option checked', () => {
    render(<FolderFilter folders={folders} total={3} value="rsoxs" onChange={() => {}} />);
    expect(screen.getByRole('radiogroup', { name: 'Folder' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /rsoxs \(2\)/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: /All \(3\)/ })).toHaveAttribute('aria-checked', 'false');
  });

  it('labels the root as Uncategorized and reports the choice (null for All)', async () => {
    const onChange = vi.fn();
    render(<FolderFilter folders={folders} total={3} value="rsoxs" onChange={onChange} />);
    await userEvent.click(screen.getByRole('radio', { name: /Uncategorized \(1\)/ }));
    expect(onChange).toHaveBeenLastCalledWith('');
    await userEvent.click(screen.getByRole('radio', { name: /All/ }));
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it('accepts a custom label and disables its options', () => {
    render(<FolderFilter folders={folders} total={3} value={null} onChange={() => {}} label="Topic folder" disabled />);
    expect(screen.getByRole('radiogroup', { name: 'Topic folder' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /rsoxs/ })).toBeDisabled();
  });
});
