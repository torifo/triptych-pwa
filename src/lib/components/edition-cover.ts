// Edition の表紙 URL を手で直す。手で直した表紙は同期で上書きしないよう、自動の印（syncHints.coverFrom）を消す。

import { isAutoCover } from '../db/schema';
import { updateEdition, updateEditionSyncHints } from '../db/store';
import { isSafeHttpUrl } from './release-view';

export { isAutoCover };

export type CoverUrlInput = { ok: true; url: string | undefined } | { ok: false; message: string };

/** 表紙 URL の入力。空は「表紙を外す」、http(s) 以外は受け付けない。 */
export function parseCoverUrlInput(raw: string): CoverUrlInput {
  const url = raw.trim();
  if (!url) return { ok: true, url: undefined };
  if (!isSafeHttpUrl(url)) return { ok: false, message: 'http:// か https:// で始まる URL を入れてください' };
  return { ok: true, url };
}

/** 手で入れた（外した）表紙を保存する。同期が上書きしないよう coverFrom を消す。 */
export async function saveEditionCover(
  editionId: string,
  url: string | undefined,
  database?: Parameters<typeof updateEdition>[2],
): Promise<void> {
  await updateEdition(editionId, { coverUrl: url }, database);
  await updateEditionSyncHints(editionId, { coverFrom: null }, database);
}
