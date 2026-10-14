// カタログの行を Library（IndexedDB）に追加する。変換と判定は register.ts。

import { db as defaultDB, type Edition, type SourceRef, type TriptychDB } from '../db/schema';
import { addSourceRef, createEdition, createWork, getWork, listEditions, listEditionsByWork } from '../db/store';
import { isRegistered, registeredKeys, toEditionInput, uniqueByRef, type EditionInput } from './register';
import type { CatalogEntry } from './types';

export interface AddResult {
  /** 追加先の Work。追加するものが無く Work も指定されなかった時は undefined。 */
  workId?: string;
  /** 新しく作った Edition と、本の参照を足した既存 Edition。 */
  added: Edition[];
  /** すでに登録済みで飛ばした行。 */
  skipped: CatalogEntry[];
}

export interface EditionPlan {
  create: EditionInput[];
  /** 既存 Edition に足す参照（本の NDL 系統参照）。 */
  attach: { editionId: string; ref: SourceRef }[];
}

const hasNdlRef = (refs: readonly SourceRef[]) => refs.some((r) => r.source === 'ndl');

/** 本の NDL 参照は同じ種別の Edition に足して1つにまとめる（新刊の同期が二重にならないように）。 */
export function planEditions(entries: readonly CatalogEntry[], workEditions: readonly Edition[]): EditionPlan {
  const create: EditionInput[] = [];
  const attach: EditionPlan['attach'] = [];
  const books: EditionInput[] = [];
  for (const entry of entries) {
    const input = toEditionInput(entry);
    if (entry.kind === 'book') books.push(input);
    else create.push({ ...input, sourceRefs: [...input.sourceRefs] });
  }
  for (const book of books) {
    const sibling = create.find((i) => i.mediaType === book.mediaType && !hasNdlRef(i.sourceRefs));
    if (sibling) {
      sibling.sourceRefs.push(...book.sourceRefs);
      continue;
    }
    const existing = workEditions.find(
      (e) => e.mediaType === book.mediaType && !hasNdlRef(e.sourceRefs) && !attach.some((a) => a.editionId === e.id),
    );
    if (existing) {
      for (const ref of book.sourceRefs) attach.push({ editionId: existing.id, ref });
      continue;
    }
    create.push(book);
  }
  return { create, attach };
}

/** entries を1つの Work に追加する。登録済みの行は飛ばし、追加するものが無ければ Work も作らない。 */
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
    const workEditions = existing ? await listEditionsByWork(workId, database) : [];
    const plan = planEditions(fresh, workEditions);

    const added: Edition[] = [];
    for (const edition of plan.create) {
      added.push(await createEdition({ workId, ...edition }, database));
    }
    for (const { editionId, ref } of plan.attach) {
      added.push(await addSourceRef(editionId, ref, database));
    }
    return { workId, added, skipped };
  });
}
