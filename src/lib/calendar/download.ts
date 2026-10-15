// 文字列をファイルとして保存させる（Blob + a[download]）。iOS Safari では .ics を開くと「カレンダーに追加」になる。

export const ICS_MIME = 'text/calendar;charset=utf-8';

export function saveTextFile(text: string, filename: string, type: string = ICS_MIME): void {
  const href = URL.createObjectURL(new Blob([text], { type }));
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = filename;
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // すぐに消すと Safari で保存が始まる前に URL が無効になることがあるので、少し待つ
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
}
