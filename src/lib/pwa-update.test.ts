import { describe, expect, it, vi } from 'vitest';
import { checkForUpdatesOnReturn } from './pwa-update';

function fakeDocument(initial: DocumentVisibilityState = 'visible') {
  const listeners = new Set<() => void>();
  return {
    visibilityState: initial,
    addEventListener: (_type: string, fn: () => void) => listeners.add(fn),
    removeEventListener: (_type: string, fn: () => void) => listeners.delete(fn),
    fire(state: DocumentVisibilityState) {
      this.visibilityState = state;
      for (const fn of listeners) fn();
    },
    get listenerCount() {
      return listeners.size;
    },
  };
}

describe('checkForUpdatesOnReturn', () => {
  it('タブに戻ってきた時だけ新しい版を確かめる', () => {
    const doc = fakeDocument();
    const registration = { update: vi.fn(() => Promise.resolve()) };
    checkForUpdatesOnReturn(registration, doc);

    doc.fire('hidden');
    expect(registration.update).not.toHaveBeenCalled();
    doc.fire('visible');
    expect(registration.update).toHaveBeenCalledTimes(1);
  });

  it('collector が止まっていて確かめられなくても例外を出さない', async () => {
    const doc = fakeDocument();
    const registration = { update: vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))) };
    checkForUpdatesOnReturn(registration, doc);

    expect(() => doc.fire('visible')).not.toThrow();
    await Promise.resolve();
    expect(registration.update).toHaveBeenCalledTimes(1);
  });

  it('返した関数で見張りをやめる', () => {
    const doc = fakeDocument();
    const registration = { update: vi.fn(() => Promise.resolve()) };
    const stop = checkForUpdatesOnReturn(registration, doc);
    stop();

    expect(doc.listenerCount).toBe(0);
    doc.fire('visible');
    expect(registration.update).not.toHaveBeenCalled();
  });
});
