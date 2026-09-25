/**
 * Раскрытие секции в два такта — порядок тактов и то, что закрытое
 * не остаётся в документе.
 *
 * В jsdom движения нет (`animate` не существует), и экран идёт коротким
 * путём: появился — исчез. Поэтому здесь движение подменено: каждый ход
 * записывается и заканчивается, когда тест его отпустит. Проверяется
 * последовательность, а не плавность — плавность меряется по кадрам
 * в браузере (`requestAnimationFrame`).
 */

import { MantineProvider } from '@mantine/core';
import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { theme } from '../theme';
import { Unfold } from './Unfold';

interface Played {
  target: string | undefined;
  from: Keyframe;
  to: Keyframe;
}

const played: Played[] = [];
const waiting: (() => void)[] = [];

/** Отпустить самый ранний неоконченный ход и дать секции сделать следующий. */
async function finish(): Promise<void> {
  await act(async () => {
    waiting.shift()?.();
    await Promise.resolve();
  });
}

beforeEach(() => {
  played.length = 0;
  waiting.length = 0;
  Object.defineProperty(Element.prototype, 'animate', {
    configurable: true,
    value(this: HTMLElement, keyframes: Keyframe[]) {
      played.push({
        target: this.dataset.unfold,
        from: keyframes[0] ?? {},
        to: keyframes[keyframes.length - 1] ?? {},
      });
      let done = () => {};
      const finished = new Promise<void>((resolve) => {
        done = resolve;
      });
      waiting.push(done);
      return { finished, cancel: () => {} };
    },
  });
  Object.defineProperty(Element.prototype, 'getAnimations', {
    configurable: true,
    value: () => [],
  });
  // Раскладки в jsdom нет: рамка «своего» размера — 200 px, а прозрачность
  // содержимого — та, что стоит у него в стиле (по умолчанию видно).
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
    this: HTMLElement,
  ) {
    const height = this.dataset.unfold === 'frame' ? 200 : 0;
    return { height, width: 0, top: 0, left: 0, right: 0, bottom: height } as DOMRect;
  });
  const real = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation((element: Element) => {
    const style = real(element);
    const opacity = (element as HTMLElement).style.opacity || '1';
    return new Proxy(style, {
      get: (target, key) => (key === 'opacity' ? opacity : (Reflect.get(target, key) as unknown)),
    });
  });
});

afterEach(() => {
  delete (Element.prototype as Partial<Element>).animate;
  delete (Element.prototype as Partial<Element>).getAnimations;
});

function Section({ open }: { open: boolean }) {
  return (
    <MantineProvider theme={theme}>
      <Unfold open={open} p="md">
        <p>содержимое секции</p>
      </Unfold>
    </MantineProvider>
  );
}

function content(): HTMLElement | null {
  return screen.queryByText('содержимое секции')?.closest('[data-unfold="content"]') ?? null;
}

describe('раскрытие секции', () => {
  it('сначала раздвигается место, потом проявляется содержимое', async () => {
    const { rerender } = render(<Section open={false} />);
    expect(content()).toBeNull();

    rerender(<Section open />);

    // Первый такт — высота рамки с нуля до своей; содержимое пока невидимо.
    expect(played).toHaveLength(1);
    expect(played[0]).toMatchObject({
      target: 'frame',
      from: { height: '0px' },
      to: { height: '200px' },
    });
    expect(content()?.style.opacity).toBe('0');

    await finish();

    // Второй такт — только когда высота дошла: проявление содержимого.
    expect(played).toHaveLength(2);
    expect(played[1]).toMatchObject({
      target: 'content',
      from: { opacity: 0 },
      to: { opacity: 1 },
    });

    await finish();
    expect(content()?.style.opacity).toBe('');
  });

  it('закрытие: содержимое гаснет, потом сдвигается место, потом секции нет вовсе', async () => {
    // Открытая с первого кадра секция не раздвигается — она часть экрана.
    const { rerender } = render(<Section open />);
    expect(played).toHaveLength(0);

    rerender(<Section open={false} />);

    expect(played).toHaveLength(1);
    expect(played[0]).toMatchObject({
      target: 'content',
      from: { opacity: 1 },
      to: { opacity: 0 },
    });
    expect(content()).not.toBeNull();

    await finish();

    expect(played).toHaveLength(2);
    expect(played[1]).toMatchObject({
      target: 'frame',
      from: { height: '200px' },
      to: { height: '0px' },
    });
    // Пока место сдвигается, секция ещё в документе.
    expect(content()).not.toBeNull();

    await finish();

    // Сдвинулась — не отрисована: не спрятана, а нет.
    expect(content()).toBeNull();
    expect(document.querySelector('[data-unfold]')).toBeNull();
  });
});
