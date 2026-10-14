import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  MAX_PER,
  buildWorksQuery,
  downloadCatalogExport,
  exportPath,
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

  it('downloads an export with the bearer token and saves it under the server filename', async () => {
    localStorage.setItem('triptych.apiToken', 'secret');
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
    await expect(downloadCatalogExport('json')).rejects.toMatchObject({ status: 404 });
  });
});
