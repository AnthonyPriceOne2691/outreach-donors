/**
 * Число в поле — строкой, как набрано: разбор и отказ словами.
 *
 * Проверка QA 10.10.2026: числовое поле Mantine стирало «12.» посреди набора и
 * склеивало цифры вокруг выброшенного знака — «1.5» → 15, «1e3» → 13, «-5» → 5.
 * Здесь — правило, по которому поле теперь говорит, что не так, а не меняет цифры.
 */

import { describe, expect, it } from 'vitest';

import {
  numberOf,
  numberRefusal,
  numberText,
  rangeText,
  sameNumber,
  validNumber,
} from './numberText';
import type { NumberRule } from './numberText';

const WHOLE: NumberRule = { decimals: 0, min: 0, max: 90 };
const FROM_ONE: NumberRule = { decimals: 0, min: 1 };
const MONEY: NumberRule = { decimals: 2, min: 0, max: 100_000 };

describe('число из набранного', () => {
  it.each([
    ['12.5', 12.5],
    ['99,5', 99.5],
    ['0.99', 0.99],
    ['1 000 000', 1_000_000],
    ['1 250,5', 1250.5],
    ['12.', 12],
    [',5', 0.5],
    ['-5', -5],
  ])('«%s» — %d', (typed, value) => {
    expect(numberOf(typed)).toBe(value);
  });

  it.each(['', '  ', '-', '1e3', '12ю5', '1.000.000', '+5', '5%'])('«%s» — не число', (typed) => {
    expect(numberOf(typed)).toBeNull();
  });
});

describe('целое поле', () => {
  it('годное — без отказа, границы включительно', () => {
    expect(numberRefusal('0', WHOLE)).toBeNull();
    expect(numberRefusal('90', WHOLE)).toBeNull();
    expect(numberRefusal(' 1 000 ', FROM_ONE)).toBeNull();
  });

  it.each(['1.5', '2,5', '1e3', '20.0', '12.'])('«%s» — отказ, а не склеенное число', (typed) => {
    expect(numberRefusal(typed, WHOLE)).toBe('Только целое число от 0 до 90');
    expect(numberRefusal(typed, FROM_ONE)).toBe('Только целое число');
  });

  it('целое за границей — отказ границами', () => {
    expect(numberRefusal('91', WHOLE)).toBe('Допустимо от 0 до 90');
    expect(numberRefusal('-5', WHOLE)).toBe('Допустимо от 0 до 90');
    expect(numberRefusal('0', FROM_ONE)).toBe('Не меньше 1');
  });

  it('пусто — не отказ этого правила: что значит пусто, решает экран', () => {
    expect(numberRefusal('', WHOLE)).toBeNull();
    expect(validNumber('', WHOLE)).toBeNull();
  });
});

describe('денежное поле', () => {
  it('центы — точкой или запятой, и середина набора «12.» — не отказ', () => {
    for (const typed of ['12.5', '99,5', '0.99', '12.', '1 250,50']) {
      expect(numberRefusal(typed, MONEY)).toBeNull();
    }
  });

  it('не число и лишние знаки после запятой — отказ словами', () => {
    // «ю» — точка на русской раскладке: «12ю5» не становится 125.
    expect(numberRefusal('12ю5', MONEY)).toBe('Только число, например 99,50');
    expect(numberRefusal('12.345', MONEY)).toBe('Не больше двух знаков после запятой');
    // «1,000» — тысяча или единица? Не угадываем: три знака после запятой — отказ.
    expect(numberRefusal('1,000', MONEY)).toBe('Не больше двух знаков после запятой');
    expect(numberRefusal('100 000,01', MONEY)).toMatch(/^Допустимо от 0 до 100\s000$/);
  });
});

describe('годное число', () => {
  it('годное — числом, негодное и пустое — `null`', () => {
    expect(validNumber('12', FROM_ONE)).toBe(12);
    expect(validNumber('1.5', FROM_ONE)).toBeNull();
    expect(validNumber('0', FROM_ONE)).toBeNull();
    expect(validNumber('99,5', MONEY)).toBe(99.5);
  });
});

describe('число в поле и сравнение', () => {
  it('в поле — по-русски, разряды обычным пробелом', () => {
    expect(numberText(10_000_000)).toBe('10 000 000');
    expect(numberText(1250.5, 2)).toBe('1 250,5');
    expect(numberText(150, 2)).toBe('150');
  });

  it('границы — числами по-русски', () => {
    expect(rangeText({ min: 0, max: 10_000_000 })).toMatch(/^от 0 до 10\s000\s000$/);
  });

  it('одно число, записанное иначе, — то же; не числа — как набраны', () => {
    expect(sameNumber('1 250,5', '1250.50')).toBe(true);
    expect(sameNumber('12.', '12')).toBe(true);
    expect(sameNumber('12', '13')).toBe(false);
    expect(sameNumber('', '')).toBe(true);
    expect(sameNumber('абв', '')).toBe(false);
  });
});
