/**
 * Лента переписки держит нижний край, когда поле ответа под ней растёт.
 *
 * Проверка QA 10.10.2026 на 1440: приложили файлы — поле ответа выросло на строку
 * значков, окно ленты в колонке высотой окна стало ниже, а прокрутка осталась прежней,
 * и низ последнего ответа ушёл под значки. Так же — от второй строки текста ответа,
 * строки отказа под полем и формы разбора под карточкой. Поле стоит в одной карточке
 * с лентой, под ней (`ThreadFeed`), поэтому любой его рост сжимает окно ленты — его
 * размер и сторожит `ResizeObserver`.
 *
 * **Держит, только если человек был внизу.** Пролистал вверх и читает старое письмо —
 * место не трогаем: прыжок вниз посреди чтения хуже закрытого края.
 */

import { type RefObject, useEffect } from 'react';

/** «Внизу» — с допуском: до самого края не докручивают на пару пикселей. */
const SLACK_PX = 24;

export function atBottom(node: HTMLElement): boolean {
  return node.scrollHeight - node.scrollTop - node.clientHeight <= SLACK_PX;
}

/**
 * Был внизу — остаётся внизу, когда окно ленты (`viewport`) меняет размер. `open` и
 * `fill` — когда окно рождается заново: оно появляется с первым письмом, а смена
 * раскладки меняет сам блок прокрутки.
 */
export function useBottomKept(
  viewport: RefObject<HTMLElement | null>,
  open: boolean,
  fill: boolean,
): void {
  useEffect(() => {
    const node = viewport.current;
    if (!open || node === null) return undefined;
    // Лента открывается на последнем письме (`ThreadFeed`) — значит, внизу.
    let kept = true;
    const onScroll = () => {
      kept = atBottom(node);
    };
    const watcher = new ResizeObserver(() => {
      if (kept) node.scrollTop = node.scrollHeight;
    });
    node.addEventListener('scroll', onScroll, { passive: true });
    watcher.observe(node);
    return () => {
      node.removeEventListener('scroll', onScroll);
      watcher.disconnect();
    };
  }, [viewport, open, fill]);
}
