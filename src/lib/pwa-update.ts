// 開きっぱなしのタブが古い版のまま残らないよう、戻ってきた時にも新しい版を確かめる（再読み込みは main.ts の registerSW）。

type VisibilityTarget = Pick<Document, 'visibilityState'> & {
  addEventListener(type: 'visibilitychange', listener: () => void): void;
  removeEventListener(type: 'visibilitychange', listener: () => void): void;
};

/** タブが見えるようになるたびに SW の更新を確かめる。返した関数で見張りをやめる。 */
export function checkForUpdatesOnReturn(
  registration: { update(): Promise<unknown> },
  doc: VisibilityTarget,
): () => void {
  const onChange = () => {
    if (doc.visibilityState !== 'visible') return;
    registration.update().catch(() => {});
  };
  doc.addEventListener('visibilitychange', onChange);
  return () => doc.removeEventListener('visibilitychange', onChange);
}
