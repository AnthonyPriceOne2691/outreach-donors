/**
 * Ширина колонки по самому длинному пункту.
 *
 * Сами пиксели здесь не проверить — jsdom не рисует текст. Проверяется
 * расчёт: холст меряет подписи, колонка равна самой широкой плюс равные
 * поля с двух сторон. Что поля на экране вышли равными, замерено
 * в браузере: у «Доменов рассылки» 24 px слева и 24,4 справа.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  COUNT_FRAME,
  FALLBACK_WIDTH,
  GROUP_INSET,
  GROUP_LINE,
  ICON_ROOM,
  navbarWidth,
} from './navWidth';

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
    // «Домены рассылки» — 15 букв, 120 px; значок с зазором; поля по 24 от
    // рамки и по 12 у колонки с каждой стороны.
    expect(navbarWidth(['Обзор', 'Домены рассылки', 'Учётки'])).toBe(
      120 + ICON_ROOM + 2 * (24 + 12),
    );
  });

  it('у оператора без длинных разделов колонка уже', () => {
    measureByLength();
    const admin = navbarWidth(['Обзор', 'Рекламодатели', 'Домены рассылки']);
    const operator = navbarWidth(['Обзор', 'Рекламодатели']);
    expect(operator).toBeLessThan(admin);
  });

  it('пункту, у которого бывает число ждущей работы, — место под самый широкий значок всегда', () => {
    measureByLength();
    // «Рекламодатели» — 104 px и значок «999+»: четыре знака по 8 px с разрядкой 0,25 px, поля
    // и кромка значка, зазор до подписи. Вместе шире «Доменов рассылки» (120).
    expect(navbarWidth(['Рекламодатели', 'Домены рассылки'], ['Рекламодатели'])).toBe(
      104 + (4 * 8 + 4 * 0.25 + COUNT_FRAME) + ICON_ROOM + 2 * (24 + 12),
    );
  });

  it('значок числа меряется своим шрифтом — жирным в 10 px, а не шрифтом подписи', () => {
    // Разбор экранов 10.11.2026: место под число было числом (48 px), а «999+» с полем занимал
    // 59 — «Рекламодатели» резались до «Рекламодате…». Здесь буква подписи — 8 px, знак значка — 6.
    const context = {
      font: '',
      measureText(text: string) {
        return { width: text.length * (this.font.startsWith('700 10px') ? 6 : 8) };
      },
    };
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
      context as unknown as CanvasRenderingContext2D,
    );

    expect(navbarWidth(['Рекламодатели'], ['Рекламодатели'])).toBe(
      104 + (4 * 6 + 4 * 0.25 + COUNT_FRAME) + ICON_ROOM + 2 * (24 + 12),
    );
  });

  it('не уже, чем нужно переключателю тем', () => {
    measureByLength();
    expect(navbarWidth(['Обзор'])).toBe(170);
  });

  it('подпись группы — в одну строку: колонка не уже «Доноров и рекламодателей»', () => {
    measureByLength();
    // 22 знака по 8 px и разрядка 0,08em от 11 px на каждый, линия после подписи и
    // поля: от рамки до подписи и у колонки с каждой стороны (11.10.2026).
    const title = 'Доноры и рекламодатели';
    expect(navbarWidth(['Обзор'], [], [title])).toBe(
      Math.ceil(title.length * 8 + title.length * 0.08 * 11) + GROUP_LINE + 2 * (GROUP_INSET + 12),
    );
    // Подпись короче пунктов колонку не расширяет.
    expect(navbarWidth(['Домены рассылки'], [], ['Продажи'])).toBe(
      navbarWidth(['Домены рассылки']),
    );
  });

  it('без холста — прежняя ширина, а не ноль', () => {
    expect(navbarWidth(['Обзор', 'Домены рассылки'])).toBe(FALLBACK_WIDTH);
  });
});
