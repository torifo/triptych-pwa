import { afterEach, describe, expect, it, vi } from 'vitest';
import { ICS_MIME, saveTextFile } from './download';

describe('saveTextFile', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('Blob の URL を a[download] で押し、後で URL を解放する', () => {
    vi.useFakeTimers();
    const blobs: Blob[] = [];
    URL.createObjectURL = vi.fn((blob: Blob) => {
      blobs.push(blob);
      return 'blob:ics';
    });
    const revoked = vi.fn();
    URL.revokeObjectURL = revoked;
    const clicked: { download: string; href: string }[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push({ download: this.download, href: this.getAttribute('href') ?? '' });
    });

    saveTextFile('BEGIN:VCALENDAR\r\n', 'triptych-20261015.ics');

    expect(clicked).toEqual([{ download: 'triptych-20261015.ics', href: 'blob:ics' }]);
    expect(blobs[0].type).toBe(ICS_MIME);
    expect(document.querySelectorAll('a[download]')).toHaveLength(0);
    expect(revoked).not.toHaveBeenCalled();
    vi.advanceTimersByTime(10_000);
    expect(revoked).toHaveBeenCalledWith('blob:ics');
  });
});
