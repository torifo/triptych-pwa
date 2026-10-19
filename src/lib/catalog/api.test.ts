import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, TOKEN_KEY } from '../api';
import {
  BUILD_COMMAND,
  DETAIL_NOT_BUILT,
  DETAIL_WORK_NOT_FOUND,
  MAX_PER,
  buildEventsQuery,
  buildWorksQuery,
  describeWorkLoadError,
  downloadCatalogExport,
  exportPath,
  fetchCatalogEvents,
  fetchCatalogWork,
  fetchCatalogWorks,
  filenameFromDisposition,
} from './api';

describe('buildWorksQuery', () => {
  it('drops empty values', () => {
    expect(buildWorksQuery({})).toEqual({});
    expect(buildWorksQuery({ q: '', has: [], page: undefined })).toEqual({});
    expect(buildWorksQuery({ q: '   ' })).toEqual({});
  });

  it('keeps only what is set', () => {
    expect(buildWorksQuery({ q: '薬屋', kind: 'anime', status: 'RELEASING', year: 2026, season: 'FALL', sort: 'recent', page: 2, per: 30 })).toEqual({
      q: '薬屋',
      kind: 'anime',
      status: 'RELEASING',
      year: '2026',
      season: 'FALL',
      sort: 'recent',
      page: '2',
      per: '30',
    });
  });

  it('trims the search text', () => {
    expect(buildWorksQuery({ q: '  転スラ ' })).toEqual({ q: '転スラ' });
  });

  it('joins has with commas, without duplicates', () => {
    expect(buildWorksQuery({ has: ['anime'] })).toEqual({ has: 'anime' });
    expect(buildWorksQuery({ has: ['anime', 'novel', 'anime', 'web-novel'] })).toEqual({ has: 'anime,novel,web-novel' });
  });

  it('caps per at the server limit and keeps it positive', () => {
    expect(buildWorksQuery({ per: 500 }).per).toBe(String(MAX_PER));
    expect(buildWorksQuery({ per: 100 }).per).toBe('100');
    expect(buildWorksQuery({ per: 0 }).per).toBe('1');
    expect(buildWorksQuery({ per: 25.9 }).per).toBe('25');
  });

  it('drops a page that is not a positive integer and a year that is not an integer', () => {
    expect(buildWorksQuery({ page: 0 })).toEqual({});
    expect(buildWorksQuery({ page: Number.NaN })).toEqual({});
    expect(buildWorksQuery({ year: Number.NaN })).toEqual({});
  });
});

describe('filenameFromDisposition', () => {
  it('reads a quoted or bare filename', () => {
    expect(filenameFromDisposition('attachment; filename="catalog-20261014.csv"', 'x')).toBe('catalog-20261014.csv');
    expect(filenameFromDisposition('attachment; filename=catalog.json', 'x')).toBe('catalog.json');
  });

  it('prefers the RFC 5987 form', () => {
    expect(filenameFromDisposition("attachment; filename=\"a.csv\"; filename*=UTF-8''%E3%82%AB%E3%82%BF.csv", 'x')).toBe('カタ.csv');
  });

  it('falls back and strips path separators', () => {
    expect(filenameFromDisposition(null, 'catalog.csv')).toBe('catalog.csv');
    expect(filenameFromDisposition('attachment', 'catalog.csv')).toBe('catalog.csv');
    expect(filenameFromDisposition('attachment; filename="../etc/passwd"', 'x')).toBe('.._etc_passwd');
  });
});

describe('requests', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    localStorage.clear();
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('calls the works endpoint with the built query', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ total: 0, page: 1, per: 30, items: [] })));
    await fetchCatalogWorks({ q: '薬屋', has: ['anime'], per: 30 });
    const url = new URL(String(fetchMock.mock.calls[0][0]), 'http://x');
    expect(url.pathname).toBe('/api/catalog/works');
    expect(Object.fromEntries(url.searchParams)).toEqual({ q: '薬屋', has: 'anime', per: '30' });
  });

  it('encodes the work id in the detail path', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: 'anilist:1', title: 't', kinds: [], entries: [], candidates: [] })));
    await fetchCatalogWork('anilist:1');
    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/catalog/works/anilist%3A1');
  });

  it('fills the original-medium fields an older collector leaves out', async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ id: 'c1', title: 't', kinds: [], entries: [], candidates: [] })));
    expect(await fetchCatalogWork('c1')).toMatchObject({ origin: null, cover: null, novelCover: null, mangaCover: null });

    const item = { id: 'c1', title: 't', kinds: ['anime'], entryCount: 1, popularity: 0, origin: 'novel' };
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ total: 2, page: 1, per: 30, items: [item, { ...item, origin: 'x' }] })));
    const page = await fetchCatalogWorks({});
    expect(page.items.map((i) => i.origin)).toEqual(['novel', null]);
    expect(page.total).toBe(2);
  });

  it('downloads an export with the bearer token and saves it under the server filename', async () => {
    localStorage.setItem(TOKEN_KEY, 'secret');
    localStorage.setItem('triptych.apiBase', 'http://collector.local');
    fetchMock.mockResolvedValue(
      new Response('id,title\n', { headers: { 'Content-Disposition': 'attachment; filename="catalog-20261014.csv"' } }),
    );
    const created = vi.fn(() => 'blob:x');
    const revoked = vi.fn();
    URL.createObjectURL = created;
    URL.revokeObjectURL = revoked;
    const clicks: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicks.push(this.download);
    });

    const name = await downloadCatalogExport('csv');

    expect(name).toBe('catalog-20261014.csv');
    expect(fetchMock).toHaveBeenCalledWith('http://collector.local' + exportPath('csv'), {
      headers: { Authorization: 'Bearer secret' },
    });
    expect(created).toHaveBeenCalledOnce();
    expect(clicks).toEqual(['catalog-20261014.csv']);
    click.mockRestore();
  });

  it('throws an ApiError when the export fails', async () => {
    fetchMock.mockResolvedValue(new Response('no', { status: 404 }));
    await expect(downloadCatalogExport('json')).rejects.toMatchObject({ status: 404, detail: undefined });
  });

  it('keeps the reason of a failed export', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: DETAIL_NOT_BUILT }), { status: 404 }));
    await expect(downloadCatalogExport('csv')).rejects.toMatchObject({ status: 404, detail: DETAIL_NOT_BUILT });
  });

  it('reads the detail of a failed work request', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: DETAIL_WORK_NOT_FOUND }), { status: 404 }));
    await expect(fetchCatalogWork('anilist:1')).rejects.toMatchObject({ status: 404, detail: DETAIL_WORK_NOT_FOUND });
  });
});

describe('describeWorkLoadError', () => {
  it('says the catalog has not been built, with the command that builds it', () => {
    const failure = describeWorkLoadError(new ApiError(404, 'x: 404', DETAIL_NOT_BUILT));
    expect(failure.kind).toBe('not-built');
    expect(failure.title).toBe('カタログ未作成');
    expect(BUILD_COMMAND).toBe('python -m backend.catalog build');
  });

  it('says the work is not in the catalog', () => {
    const failure = describeWorkLoadError(new ApiError(404, 'x: 404', DETAIL_WORK_NOT_FOUND));
    expect(failure.kind).toBe('not-found');
    expect(failure.title).toBe('作品が見つかりません');
    expect(failure.message).toContain('作品 ID');
  });

  it('does not mistake other failures for either', () => {
    const noDetail = describeWorkLoadError(new ApiError(404, 'x: 404')); // a collector without the catalog routes, a proxy
    const otherDetail = describeWorkLoadError(new ApiError(404, 'x: 404', 'Not Found'));
    const server = describeWorkLoadError(new ApiError(500, 'x: 500', DETAIL_NOT_BUILT));
    const network = describeWorkLoadError(new TypeError('Failed to fetch'));
    for (const failure of [noDetail, otherDetail, server, network]) {
      expect(failure.kind).toBe('error');
      expect(failure.title).toBe('作品を取得できませんでした');
    }
    expect(noDetail.message).toContain('404');
    expect(server.message).toContain('500');
    expect(network.message).not.toMatch(/\d{3}/);
  });

  it('tells the three apart by kind', () => {
    const kinds = [
      new ApiError(404, 'x', DETAIL_NOT_BUILT),
      new ApiError(404, 'x', DETAIL_WORK_NOT_FOUND),
      new ApiError(503, 'x'),
    ].map((err) => describeWorkLoadError(err).kind);
    expect(new Set(kinds).size).toBe(3);
  });
});

describe('buildEventsQuery', () => {
  it('keeps the range and joins the kinds with commas, without duplicates', () => {
    expect(buildEventsQuery({ from: '2026-10-15', to: '2026-10-21' })).toEqual({ from: '2026-10-15', to: '2026-10-21' });
    expect(buildEventsQuery({ from: '2026-10-15', to: '2026-10-21', kinds: [] })).toEqual({
      from: '2026-10-15',
      to: '2026-10-21',
    });
    expect(buildEventsQuery({ from: '2026-10-01', to: '2026-10-31', kinds: ['anime', 'book', 'anime'] })).toEqual({
      from: '2026-10-01',
      to: '2026-10-31',
      kind: 'anime,book',
    });
  });
});

describe('fetchCatalogEvents', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    localStorage.clear();
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('calls the events endpoint with the range and kinds', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ items: [], truncated: false })));
    const page = await fetchCatalogEvents({ from: '2026-10-15', to: '2026-10-21', kinds: ['anime'] });
    const url = new URL(String(fetchMock.mock.calls[0][0]), 'http://x');
    expect(url.pathname).toBe('/api/catalog/events');
    expect(Object.fromEntries(url.searchParams)).toEqual({ from: '2026-10-15', to: '2026-10-21', kind: 'anime' });
    expect(page).toEqual({ items: [], truncated: false });
  });

  it('keeps the reason when the catalog has not been built', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: DETAIL_NOT_BUILT }), { status: 404 }));
    await expect(fetchCatalogEvents({ from: '2026-10-15', to: '2026-10-21' })).rejects.toMatchObject({
      status: 404,
      detail: DETAIL_NOT_BUILT,
    });
  });
});
