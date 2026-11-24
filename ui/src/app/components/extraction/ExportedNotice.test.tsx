import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ExportedNotice, formatBytes } from './ExportedNotice';

describe('ExportedNotice', () => {
  it('formats sizes', () => {
    expect(formatBytes(812)).toBe('812 B');
    expect(formatBytes(2048)).toBe('2.0 KB');
    expect(formatBytes(2423054)).toBe('2.3 MB');
  });

  it('says where the file went and links the download', () => {
    render(<ExportedNotice result={{ name: 'tomo_test', file_name: 'matkg_tomo_test.json', size_bytes: 2423054 }} />);
    const notice = screen.getByTestId('export-notice');
    expect(notice).toHaveTextContent('Exported matkg_tomo_test.json (2.3 MB)');
    expect(notice).toHaveTextContent('stored on the server, in the release folder');
    expect(notice).toHaveTextContent('Publish to Tiled and Continue existing KG');
    expect(notice).toHaveTextContent('nothing was downloaded to your computer');
    const link = screen.getByRole('link', { name: /Download matkg_tomo_test\.json/ });
    expect(link).toHaveAttribute('href', expect.stringContaining('/v1/library/kgs/tomo_test/download'));
    expect(link).toHaveAttribute('download', 'matkg_tomo_test.json');
  });

  it('works with an older server that sends no size, and shows nothing before an export', () => {
    const { rerender } = render(<ExportedNotice result={{ name: 'a', file_name: 'matkg_a.json' }} />);
    expect(screen.getByTestId('export-notice')).toHaveTextContent('Exported matkg_a.json. It is stored');
    rerender(<ExportedNotice result={null} />);
    expect(screen.queryByTestId('export-notice')).not.toBeInTheDocument();
  });
});
