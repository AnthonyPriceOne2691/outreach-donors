/**
 * Подъём колонки меню вслед за уехавшей шапкой.
 *
 * Раскладку jsdom не считает, поэтому проверяется то, из чего она
 * складывается: сколько шапки уехало при данной прокрутке и что это число
 * доходит до корня рамы при каждой прокрутке, а после отписки — нет.
 */

import { afterEach, describe, expect, it } from 'vitest';

import { followScroll, liftFor } from './shellLift';

function scrollTo(y: number) {
  Object.defineProperty(window, 'scrollY', { value: y, configurable: true });
  window.dispatchEvent(new Event('scroll'));
}

afterEach(() => scrollTo(0));

describe('подъём колонки меню', () => {
  it('колонка поднимается на столько, на сколько уехала шапка, и не дальше', () => {
    expect(liftFor(0, 68)).toBe(0);
    expect(liftFor(30, 68)).toBe(30);
    expect(liftFor(68, 68)).toBe(68);
    expect(liftFor(900, 68)).toBe(68);
    // Отскок у верхнего края (macOS) даёт отрицательную прокрутку — не опускаться.
    expect(liftFor(-40, 68)).toBe(0);
  });

  it('смещение доходит до рамы при прокрутке, после отписки — нет', () => {
    const root = document.createElement('div');
    const stop = followScroll(root, 68);
    expect(root.style.getPropertyValue('--shell-lift')).toBe('0px');

    scrollTo(25);
    expect(root.style.getPropertyValue('--shell-lift')).toBe('25px');
    scrollTo(500);
    expect(root.style.getPropertyValue('--shell-lift')).toBe('68px');

    stop();
    scrollTo(10);
    expect(root.style.getPropertyValue('--shell-lift')).toBe('68px');
  });
});
