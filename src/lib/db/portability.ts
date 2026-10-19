// エクスポート/インポート（NFR: データ保全）。
// 全テーブル（works/editions/releases/detections/meta）を単一JSONに束ねる。

import { db as defaultDB, dropMisplacedAnimeCover, type TriptychDB } from './schema';
import type { Work, Edition, Release, DetectionEvent, MetaEntry } from './schema';

export const EXPORT_VERSION = 1 as const;

export interface ExportTables {
  works: Work[];
  editions: Edition[];
  releases: Release[];
  detections: DetectionEvent[];
  meta: MetaEntry[];
}

export interface ExportPayload {
  version: typeof EXPORT_VERSION;
  exportedAt: string; // ISO 8601
  tables: ExportTables;
}

export type ImportMode = 'replace';

const TABLE_KEYS: (keyof ExportTables)[] = ['works', 'editions', 'releases', 'detections', 'meta'];

/** インポート/エクスポートで検出した不正データを表す例外。理由は message に含む。 */
export class PortabilityError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PortabilityError';
  }
}

function nowISO(): string {
  return new Date().toISOString();
}

/**
 * 全テーブルを JSON 化可能なペイロードとして返す。
 * meta に lastExportAt を記録してから読み出すため、返却データにも反映される。
 */
export async function exportAll(database: TriptychDB = defaultDB): Promise<ExportPayload> {
  const exportedAt = nowISO();
  await database.meta.put({ key: 'lastExportAt', value: exportedAt });

  const [works, editions, releases, detections, meta] = await Promise.all([
    database.works.toArray(),
    database.editions.toArray(),
    database.releases.toArray(),
    database.detections.toArray(),
    database.meta.toArray(),
  ]);

  return {
    version: EXPORT_VERSION,
    exportedAt,
    tables: { works, editions, releases, detections, meta },
  };
}

/** 直近のエクスポート時刻（meta.lastExportAt）。未実施なら undefined。 */
export async function getLastExportAt(
  database: TriptychDB = defaultDB,
): Promise<string | undefined> {
  const entry = await database.meta.get('lastExportAt');
  return typeof entry?.value === 'string' ? entry.value : undefined;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** テーブル形状の軽い検証: 配列であること、各行が想定キーを持つこと。 */
function validateTableRows(tableName: keyof ExportTables, rows: unknown[]): void {
  const idKey = tableName === 'meta' ? 'key' : 'id';
  rows.forEach((row, index) => {
    if (!isPlainObject(row) || typeof row[idKey] !== 'string') {
      throw new PortabilityError(
        `不正なエクスポートデータ: tables.${tableName}[${index}] に ${idKey} がありません`,
      );
    }
  });
}

function validatePayload(parsed: unknown): asserts parsed is ExportPayload {
  if (!isPlainObject(parsed)) {
    throw new PortabilityError('不正なエクスポートデータ: JSONオブジェクトではありません');
  }
  if (parsed.version !== EXPORT_VERSION) {
    throw new PortabilityError(
      `未対応のバージョンです: ${String(parsed.version)}（対応: ${EXPORT_VERSION}）`,
    );
  }
  if (typeof parsed.exportedAt !== 'string') {
    throw new PortabilityError('不正なエクスポートデータ: exportedAt がありません');
  }
  if (!isPlainObject(parsed.tables)) {
    throw new PortabilityError('不正なエクスポートデータ: tables がありません');
  }
  for (const key of TABLE_KEYS) {
    const rows = parsed.tables[key];
    if (!Array.isArray(rows)) {
      throw new PortabilityError(`不正なエクスポートデータ: tables.${key} が配列ではありません`);
    }
    validateTableRows(key, rows);
  }
}

/**
 * JSON 文字列からインポートする。
 * mode 'replace': 全テーブルを消去してからインポートデータを投入する。
 * 不正な JSON / 想定外の形状は PortabilityError を投げる（呼び出し側は理由を表示できる）。
 */
export async function importAll(
  json: string,
  mode: ImportMode = 'replace',
  database: TriptychDB = defaultDB,
): Promise<void> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch (err) {
    throw new PortabilityError(`不正なJSONです: ${(err as Error).message}`);
  }

  validatePayload(parsed);

  if (mode !== 'replace') {
    throw new PortabilityError(`未対応のインポートモードです: ${String(mode)}`);
  }

  const { tables } = parsed;
  // 修正前のバックアップで本に入っていたアニメの表紙を持ち込まない（v3 の移行と同じ）
  for (const edition of tables.editions) dropMisplacedAnimeCover(edition);

  await database.transaction(
    'rw',
    database.works,
    database.editions,
    database.releases,
    database.detections,
    database.meta,
    async () => {
      await Promise.all([
        database.works.clear(),
        database.editions.clear(),
        database.releases.clear(),
        database.detections.clear(),
        database.meta.clear(),
      ]);
      await Promise.all([
        tables.works.length > 0 ? database.works.bulkAdd(tables.works) : Promise.resolve(),
        tables.editions.length > 0 ? database.editions.bulkAdd(tables.editions) : Promise.resolve(),
        tables.releases.length > 0 ? database.releases.bulkAdd(tables.releases) : Promise.resolve(),
        tables.detections.length > 0
          ? database.detections.bulkAdd(tables.detections)
          : Promise.resolve(),
        tables.meta.length > 0 ? database.meta.bulkAdd(tables.meta) : Promise.resolve(),
      ]);
    },
  );
}
