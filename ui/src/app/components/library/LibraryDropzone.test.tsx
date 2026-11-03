import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { LibraryDropzone } from './LibraryDropzone';

const pdf = new File(['%PDF'], 'a.pdf', { type: 'application/pdf' });
const txt = new File(['x'], 'b.txt', { type: 'text/plain' });

function setup(props: Partial<React.ComponentProps<typeof LibraryDropzone>> = {}) {
  const onPdfFiles = vi.fn();
  const onRejected = vi.fn();
  render(<LibraryDropzone onPdfFiles={onPdfFiles} onRejected={onRejected} {...props} />);
  return { onPdfFiles, onRejected, zone: screen.getByRole('button', { name: /upload pdf papers/i }) };
}

describe('LibraryDropzone', () => {
  it('accepts dropped PDFs and rejects non-PDFs client-side', () => {
    const { onPdfFiles, onRejected, zone } = setup();
    fireEvent.drop(zone, { dataTransfer: { files: [pdf, txt] } });
    expect(onPdfFiles).toHaveBeenCalledWith([pdf]);
    expect(onRejected).toHaveBeenCalledWith(['b.txt']);
  });

  it('uploads via the Browse input', async () => {
    const { onPdfFiles } = setup();
    await userEvent.upload(screen.getByTestId('library-file-input'), pdf);
    expect(onPdfFiles).toHaveBeenCalledWith([pdf]);
  });

  it('opens the file picker from the keyboard (Enter and Space)', async () => {
    const { zone } = setup();
    const click = vi.spyOn(screen.getByTestId('library-file-input'), 'click');
    zone.focus();
    await userEvent.keyboard('{Enter}');
    await userEvent.keyboard(' ');
    expect(click).toHaveBeenCalledTimes(2);
  });

  it('marks the zone as dragging while a drag is over it', () => {
    const { zone } = setup();
    fireEvent.dragOver(zone);
    expect(zone).toHaveAttribute('data-dragging', 'true');
    fireEvent.dragLeave(zone);
    expect(zone).toHaveAttribute('data-dragging', 'false');
  });

  it('disabled: ignores drops, is not focusable, aria-disabled, dimmed', () => {
    const { onPdfFiles, zone } = setup({ disabled: true });
    fireEvent.drop(zone, { dataTransfer: { files: [pdf] } });
    expect(onPdfFiles).not.toHaveBeenCalled();
    expect(zone).toHaveAttribute('aria-disabled', 'true');
    expect(zone).toHaveAttribute('tabindex', '-1');
    expect(zone).toHaveClass('opacity-50', 'cursor-not-allowed');
    expect(screen.getByRole('button', { name: 'Browse' })).toBeDisabled();
  });

  it('busy: exposes aria-busy', () => {
    const { zone } = setup({ busy: true });
    expect(zone).toHaveAttribute('aria-busy', 'true');
  });
});
