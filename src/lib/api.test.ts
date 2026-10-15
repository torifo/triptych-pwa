import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, TOKEN_KEY, apiGet, readErrorDetail, setApiToken } from './api';

describe('ApiError', () => {
  it('still works with a status and a message only', () => {
    const err = new ApiError(404, 'x: 404');
    expect(err).toBeInstanceOf(Error);
    expect(err.status).toBe(404);
    expect(err.message).toBe('x: 404');
    expect(err.detail).toBeUndefined();
  });

  it('can carry the detail the server sent', () => {
    expect(new ApiError(404, 'x: 404', 'work not found').detail).toBe('work not found');
  });
});

describe('readErrorDetail', () => {
  it('reads a string detail', async () => {
    const res = new Response(JSON.stringify({ detail: 'catalog not built' }), { status: 404 });
    expect(await readErrorDetail(res)).toBe('catalog not built');
  });

  it('ignores details that are not a string (a 422 lists its errors) and bodies that are not JSON', async () => {
    expect(await readErrorDetail(new Response(JSON.stringify({ detail: [{ msg: 'x' }] }), { status: 422 }))).toBeUndefined();
    expect(await readErrorDetail(new Response(JSON.stringify({ message: 'x' }), { status: 500 }))).toBeUndefined();
    expect(await readErrorDetail(new Response('<html>Bad Gateway</html>', { status: 502 }))).toBeUndefined();
    expect(await readErrorDetail(new Response(JSON.stringify(null), { status: 500 }))).toBeUndefined();
    expect(await readErrorDetail(new Response(null, { status: 404 }))).toBeUndefined();
  });
});

describe('apiGet', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    localStorage.clear();
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('puts the status and the server detail on the ApiError', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: 'work not found' }), { status: 404 }));
    await expect(apiGet('/api/catalog/works/x')).rejects.toMatchObject({
      name: 'Error',
      status: 404,
      message: '/api/catalog/works/x: 404',
      detail: 'work not found',
    });
  });

  it('has no detail when the error body says nothing usable', async () => {
    fetchMock.mockResolvedValue(new Response('oops', { status: 500 }));
    const err = await apiGet('/api/x').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(500);
    expect((err as ApiError).detail).toBeUndefined();
  });

  it('sends the bearer token stored under TOKEN_KEY', async () => {
    setApiToken('secret');
    expect(localStorage.getItem(TOKEN_KEY)).toBe('secret');
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ ok: true })));
    expect(await apiGet('/api/x')).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledWith('/api/x', { headers: { Authorization: 'Bearer secret' } });
  });
});
