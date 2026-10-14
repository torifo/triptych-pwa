// カタログの行を Library（IndexedDB）に追加する。変換と判定は register.ts。

import { db as defaultDB, type Edition, type TriptychDB } from '../db/schema';
import { createEdition, createWork, getWork, listEditions } from '../db/store';
import { isRegistered, registeredKeys, toEditionInput, uniqueByRef } from './register';
import type { CatalogEntry } from './types';

export interface AddResult {
  /** 追加先の Work。追加するものが無く Work も指定されなかった時は undefined。 */
  workId?: string;
  added: Edition[];
  /** すでに登録済みで飛ばした行。 */
  skipped: CatalogEntry[];
}

/**
 * entries を1つの Work にまとめて追加する。
 * workId を渡せばその Work に、無ければ title で Work を新しく作る。登録済みの行（同じ source・externalId、
 * 本は同じシリーズ）は飛ばし、追加するものが無ければ Work も作らない。
 */
export async function addEntriesToLibrary(
  input: { title: string; entries: readonly CatalogEntry[]; workId?: string },
  database: TriptychDB = defaultDB,
): Promise<AddResult> {
  return database.transaction('rw', database.works, database.editions, async () => {
    const keys = registeredKeys(await listEditions(database));
    const unique = uniqueByRef(input.entries);
    const fresh = unique.filter((entry) => !isRegistered(entry, keys));
    const skipped = input.entries.filter((entry) => !fresh.includes(entry));

    const existing = input.workId ? await getWork(input.workId, database) : undefined;
    if (fresh.length === 0) return { workId: existing?.id, added: [], skipped };

    const workId = existing?.id ?? (await createWork({ title: input.title }, database)).id;
    const added: Edition[] = [];
    for (const entry of fresh) {
      added.push(await createEdition({ workId, ...toEditionInput(entry) }, database));
    }
    return { workId, added, skipped };
  });
}
