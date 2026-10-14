import type { CatalogEntry } from './types';

/** テスト用の CatalogEntry。必須項目だけ埋める。 */
export function makeEntry(over: Partial<CatalogEntry> & Pick<CatalogEntry, 'source' | 'externalId' | 'kind'>): CatalogEntry {
  return {
    id: `${over.source}:${over.externalId}`,
    title: over.title ?? `${over.source}-${over.externalId}`,
    synonyms: [],
    genres: [],
    studios: [],
    creators: [],
    extra: {},
    ...over,
  };
}
