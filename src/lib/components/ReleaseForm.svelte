<script lang="ts">
  import type { DatePrecision, Edition, Release } from '../db/schema';
  import { todayJst } from '../dates';
  import {
    PRECISION_LABELS,
    RELEASE_KINDS_BY_MEDIA,
    RELEASE_KIND_LABELS,
  } from './labels';
  import {
    buildReleaseDate,
    parseTimeInput,
    precisionOf,
    releaseKindOptions,
    splitDateFields,
    type DateFields,
    type ReleaseFormValue,
  } from './release-view';

  let {
    mediaType,
    release,
    onsubmit,
    oncancel,
  }: {
    mediaType: Edition['mediaType'];
    /** 渡すと編集、省略すると新規の手動追加 */
    release?: Release;
    onsubmit: (value: ReleaseFormValue) => Promise<void>;
    oncancel: () => void;
  } = $props();

  const PRECISIONS: DatePrecision[] = ['day', 'month', 'year'];
  const MONTHS = Array.from({ length: 12 }, (_, i) => String(i + 1));

  // 初期値は編集対象（なければ今日）。以後はフォーム内で独立して編集する。
  // ($state の初期化子で props を直接参照すると警告になるため、mount 時に一度だけ流し込む)
  let kind = $state<Release['kind']>('volume');
  let label = $state('');
  let precision = $state<DatePrecision>('day');
  let dayValue = $state('');
  let yearValue = $state('');
  let monthValue = $state('1');
  let timeValue = $state('');
  let busy = $state(false);
  let errorMessage = $state<string | null>(null);

  const kindOptions = $derived(releaseKindOptions(RELEASE_KINDS_BY_MEDIA[mediaType], release?.kind));

  $effect.pre(() => {
    // mount 時のみ: release が差し替わった時だけ入れ直す
    const source = release;
    kind = source?.kind ?? RELEASE_KINDS_BY_MEDIA[mediaType][0];
    label = source?.label ?? '';
    precision = source ? precisionOf(source) : 'day';
    const fields = splitDateFields(source?.date ?? todayJst());
    dayValue = fields?.day ?? '';
    yearValue = fields?.year ?? '';
    monthValue = fields?.month ?? '1';
    timeValue = source?.time ?? '';
    errorMessage = null;
  });

  const fields = $derived<DateFields>({ day: dayValue, year: yearValue, month: monthValue });
  const date = $derived(buildReleaseDate(precision, fields));
  const time = $derived(precision === 'day' ? parseTimeInput(timeValue) : parseTimeInput(''));
  const trimmedLabel = $derived(label.trim());
  const canSubmit = $derived(!busy && trimmedLabel !== '' && date !== null && time.ok);

  function changePrecision(next: DatePrecision) {
    // いま入力中の日付を、切り替え先の入力欄にも引き継ぐ
    const current = splitDateFields(date ?? '');
    if (current) {
      dayValue = current.day;
      yearValue = current.year;
      monthValue = current.month;
    }
    precision = next;
  }

  async function submit(event: Event) {
    event.preventDefault();
    if (!canSubmit || date === null || !time.ok) return;
    busy = true;
    errorMessage = null;
    try {
      await onsubmit({
        kind,
        label: trimmedLabel,
        date,
        precision,
        time: time.value,
      });
    } catch (err) {
      errorMessage = err instanceof Error ? err.message : '保存できませんでした';
    } finally {
      busy = false;
    }
  }
</script>

<form class="release-form flex flex-col gap-1.5 p-2" onsubmit={submit}>
  <div class="grid grid-cols-2 gap-1.5">
    <label class="flex flex-col gap-0.5" style="min-width: 0;">
      <span class="type-label">種類</span>
      <select aria-label="Release の種類" class="field" bind:value={kind}>
        {#each kindOptions as k (k)}
          <option value={k}>{RELEASE_KIND_LABELS[k]}</option>
        {/each}
      </select>
    </label>
    <label class="flex flex-col gap-0.5" style="min-width: 0;">
      <span class="type-label">精度</span>
      <select
        aria-label="日付の精度"
        class="field"
        value={precision}
        onchange={(e) => changePrecision((e.currentTarget as HTMLSelectElement).value as DatePrecision)}
      >
        {#each PRECISIONS as p (p)}
          <option value={p}>{PRECISION_LABELS[p]}</option>
        {/each}
      </select>
    </label>
  </div>

  <label class="flex flex-col gap-0.5" style="min-width: 0;">
    <span class="type-label">ラベル</span>
    <input
      class="field"
      aria-label="Release のラベル"
      placeholder={mediaType === 'anime' ? '第2期 第1話' : '第12巻'}
      bind:value={label}
    />
  </label>

  <div class="flex flex-col gap-0.5" style="min-width: 0;">
    <span class="type-label">日付</span>
    {#if precision === 'day'}
      <input type="date" class="field date-input" aria-label="日付" bind:value={dayValue} />
    {:else}
      <div class="flex items-center gap-1" style="min-width: 0;">
        <input
          type="number"
          inputmode="numeric"
          min="1900"
          max="2999"
          class="field"
          style="width: 5rem; flex: none;"
          aria-label="年"
          bind:value={yearValue}
        />
        <span class="text-[11px]" style="color: var(--muted); white-space: nowrap;">年</span>
        {#if precision === 'month'}
          <select class="field" style="width: 4rem; flex: none;" aria-label="月" bind:value={monthValue}>
            {#each MONTHS as m (m)}
              <option value={m}>{m}</option>
            {/each}
          </select>
          <span class="text-[11px]" style="color: var(--muted); white-space: nowrap;">月</span>
        {/if}
      </div>
    {/if}
  </div>

  <label class="flex flex-col gap-0.5" style="max-width: 8rem; min-width: 0;">
    <span class="type-label">時刻（任意）</span>
    <input
      class="field"
      inputmode="numeric"
      placeholder="25:30"
      aria-label="時刻（任意・25:30 の形式）"
      disabled={precision !== 'day'}
      bind:value={timeValue}
    />
  </label>

  {#if !time.ok}
    <p class="text-[11px]" style="color: var(--fg);">時刻は 25:30 の形式（0〜29 時）で入力してください</p>
  {/if}
  {#if release && release.origin !== 'manual'}
    <p class="text-[11px]" style="color: var(--muted);">編集すると手動扱いになり、同期で上書きされません</p>
  {/if}
  {#if errorMessage}
    <p class="text-[11px]" style="color: var(--fg);" role="alert">{errorMessage}</p>
  {/if}

  <div class="flex items-center gap-2">
    <button type="submit" class="btn btn-main" disabled={!canSubmit}>
      {release ? '保存' : '追加'}
    </button>
    <button type="button" class="btn" disabled={busy} onclick={oncancel}>キャンセル</button>
  </div>
</form>

<style>
  .release-form {
    border: 1px solid var(--fg);
    min-width: 0;
  }
  .field {
    width: 100%;
    min-width: 0;
    padding: 0.25rem 0.375rem;
    font-size: 0.8125rem;
    border: 1px solid var(--line);
    background: var(--bg);
    color: var(--fg);
    border-radius: 0;
  }
  .field:disabled {
    color: var(--muted);
  }
  .btn {
    padding: 0.25rem 0.5rem;
    font-size: 0.6875rem;
    border: 1px solid var(--line);
    color: var(--fg);
    background: transparent;
    white-space: nowrap;
  }
  .btn-main {
    border-color: var(--fg);
  }
  .btn:disabled {
    color: var(--muted);
  }
  /* ダークモードでは日付ピッカーとセレクトの選択肢も暗色にする（app.css は .dark クラスで切り替えている） */
  :global(.dark) .field {
    color-scheme: dark;
  }
</style>
