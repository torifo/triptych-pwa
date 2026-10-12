// 同期状態を UI に公開する薄いストア（Svelte 5 の $state を使用）。
//
// 意図的に engine.ts からは import しない: このリポジトリの vitest.config.ts
// は svelte プラグインを通さないため、.svelte.ts を vitest 経由で import
// すると `$state` のコンパイル変換が効かず失敗する（engine.test.ts で確認
// 済み）。UI 実装タスク（App.svelte 配線）が runSync() の呼び出し前後で
// beginSync/completeSync/failSync を呼び、このストアを更新する想定。

import type { Source } from '../db/schema';

export interface SyncSourceError {
  message: string;
  occurredAt: string; // ISO 8601
}

/** ソース名、または検知（'detect'）。 */
export type SyncErrorKey = Source | 'detect';

export type SyncErrors = Partial<Record<SyncErrorKey, SyncSourceError>>;

export interface SyncStatusState {
  running: boolean;
  lastSyncAt: string | null;
  errors: SyncErrors;
}

export const syncStatus = $state<SyncStatusState>({
  running: false,
  lastSyncAt: null,
  errors: {},
});

/** 同期開始時に呼ぶ。 */
export function beginSync(): void {
  syncStatus.running = true;
}

/** 同期完了時に呼ぶ（成功/一部ソース失敗どちらも含む）。 */
export function completeSync(lastSyncAt: string, errors: SyncErrors): void {
  syncStatus.running = false;
  syncStatus.lastSyncAt = lastSyncAt;
  syncStatus.errors = errors;
}

/** スロットル等で実行されなかった場合や、致命的失敗時に呼ぶ。 */
export function failSync(errors: SyncErrors): void {
  syncStatus.running = false;
  syncStatus.errors = errors;
}
