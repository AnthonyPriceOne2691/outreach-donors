/**
 * Лента переписки держит нижний край, когда поле ответа под ней растёт (проверка QA
 * 10.10.2026: значки приложенных файлов закрывали низ последнего ответа).
 *
 * jsdom не раскладывает страницу: размеры окна ленты задаются руками, а наблюдатель
 * размера — записывающий, и тест зовёт его сам, как браузер после раскладки.
 */

import { fireEvent } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { renderWith } from '../test/render';
import { letter, reply } from '../test/threadFixtures';
import { ThreadFeed } from './ThreadFeed';

/** Наблюдатели размера, заведённые за тест, — с тем, за чем они смотрят. */
const watchers: { callback: ResizeObserverCallback; targets: Element[] }[] = [];

class RecordingObserver {
  private readonly own: { callback: ResizeObserverCallback; targets: Element[] };

  constructor(callback: ResizeObserverCallback) {
    this.own = { callback, targets: [] };
    watchers.push(this.own);
  }

  observe(target: Element) {
    this.own.targets.push(target);
  }

  unobserve() {}

  disconnect() {
    this.own.targets = [];
  }
}

beforeEach(() => {
  watchers.length = 0;
  vi.stubGlobal('ResizeObserver', RecordingObserver);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Лента рядом со списком: окно высотой колонки, поле ответа — под ним. */
function openFeed(): HTMLElement {
  const { container } = renderWith(
    <ThreadFeed
      letters={[letter()]}
      incoming={[reply()]}
      corridor={{ min: 0.15, max: 0.25 }}
      activeId={null}
      reviewable={() => true}
      sales={false}
      onPick={() => {}}
      fill
      composer={<div>поле ответа</div>}
    />,
  );
  const viewport = container.querySelector<HTMLElement>('.mantine-ScrollArea-viewport');
  if (viewport === null) throw new Error('окна ленты на экране нет');
  return viewport;
}

/** Высота окна и содержимого — как после раскладки. */
function size(viewport: HTMLElement, content: number, window: number) {
  Object.defineProperty(viewport, 'scrollHeight', { configurable: true, value: content });
  Object.defineProperty(viewport, 'clientHeight', { configurable: true, value: window });
}

/** Браузер пересчитал раскладку: окно ленты сменило размер. */
function relayout(viewport: HTMLElement) {
  for (const watcher of watchers.filter((one) => one.targets.includes(viewport))) {
    watcher.callback([], {} as ResizeObserver);
  }
}

describe('нижний край ленты', () => {
  it('была внизу — поле ответа выросло, лента осталась внизу', () => {
    const viewport = openFeed();
    size(viewport, 1000, 400);
    viewport.scrollTop = 600;
    fireEvent.scroll(viewport);

    // Приложили файл: строка значков над полем, окно ленты ниже на 60 px.
    size(viewport, 1000, 340);
    relayout(viewport);

    expect(viewport.scrollTop).toBe(1000);
  });

  it('пролистали вверх, читают — поле выросло, место чтения не трогаем', () => {
    const viewport = openFeed();
    size(viewport, 1000, 400);
    viewport.scrollTop = 200;
    fireEvent.scroll(viewport);

    size(viewport, 1000, 340);
    relayout(viewport);

    expect(viewport.scrollTop).toBe(200);
  });
});
