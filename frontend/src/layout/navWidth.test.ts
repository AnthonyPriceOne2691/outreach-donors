/**
 * Ширина колонки по самому длинному пункту.
 *
 * Сами пиксели здесь не проверить — jsdom не рисует текст. Проверяется
 * расчёт: холст меряет подписи, колонка равна самой широкой плюс равные
 * поля с двух сторон. Что поля на экране вышли равными, замерено
 * в браузере: у «Доменов рассылки» 24 px слева и 24,4 справа.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import { FALLBACK_WIDTH, navbarWidth } from './navWidth';

/** Холст, у которого каждая буква шириной 8 px. */
function measureByLength() {
  const context = {
    font: '',
    measureText: (text: string) => ({ width: text.length * 8 }),
  } as unknown as CanvasRenderingContext2D;
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ширина боковой колонки', () => {
  it('равна самому длинному пункту и равным полям слева и справа', () => {
    measureByLength();
    // «Домены рассылки» — 15 букв, 120 px; поля по 24 от рамки и по 12
    // у колонки с каждой стороны.
    expect(navbarWidth(['Обзор', 'Домены рассылки', 'Учётки'])).toBe(120 + 2 * (24 + 12));
  });

  it('у оператора без длинных разделов колонка уже', () => {
    measureByLength();
    const admin = navbarWidth(['Обзор', 'Рекламодатели', 'Домены рассылки']);
    const operator = navbarWidth(['Обзор', 'Рекламодатели']);
    expect(operator).toBeLessThan(admin);
  });

  it('не уже, чем нужно переключателю тем', () => {
    measureByLength();
    expect(navbarWidth(['Обзор'])).toBe(170);
  });

  it('без холста — прежняя ширина, а не ноль', () => {
    expect(navbarWidth(['Обзор', 'Домены рассылки'])).toBe(FALLBACK_WIDTH);
  });
});
