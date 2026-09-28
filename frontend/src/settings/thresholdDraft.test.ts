/**
 * Проверка порога — то же правило, что у схемы сервера: целое, в границах
 * включительно, пустое поле — не ноль.
 */

import { describe, expect, it } from 'vitest';

import { bodyOf, fieldRefusal, rangeText, typed } from './thresholdDraft';
import type { ThresholdLimits } from './thresholdDraft';

const LIMITS: ThresholdLimits = {
  min_dr: { min: 0, max: 90 },
  min_org_traffic: { min: 0, max: 10_000_000 },
  min_refdomains: { min: 0, max: 1_000_000 },
  min_keywords: { min: 0, max: 1_000_000 },
};

describe('поле порога', () => {
  it('сами границы допустимы, шаг за ними — нет', () => {
    expect(fieldRefusal(0, LIMITS.min_dr)).toBeNull();
    expect(fieldRefusal(90, LIMITS.min_dr)).toBeNull();
    expect(fieldRefusal(91, LIMITS.min_dr)).toBe('Допустимо от 0 до 90');
    expect(fieldRefusal(-1, LIMITS.min_dr)).toBe('Допустимо от 0 до 90');
  });

  it('пустое поле просит число, а не становится нулём', () => {
    expect(typed('')).toBe('');
    expect(typed('  ')).toBe('');
    expect(fieldRefusal('', LIMITS.min_dr)).toBe('Впишите число');
  });

  it('дробь не проходит: DR 20,5 в базе не бывает', () => {
    expect(fieldRefusal(20.5, LIMITS.min_dr)).toBe('Только целое число');
  });

  it('число длиннее безопасного целого остаётся числом и не проходит границы', () => {
    // `NumberInput` отдаёт такое число строкой; обрезать его до края
    // диапазона молча значило бы сохранить то, чего не набирали.
    const huge = typed('123456789012345678901');
    expect(typeof huge).toBe('number');
    expect(fieldRefusal(huge, LIMITS.min_org_traffic)).toMatch(/^Допустимо от 0 до 10/);
  });

  it('разряды в наборе не мешают: «1 000 000» — миллион', () => {
    expect(typed('1 000 000')).toBe(1_000_000);
  });

  it('границы — числами по-русски', () => {
    expect(rangeText(LIMITS.min_org_traffic)).toMatch(/^от 0 до 10\s000\s000$/);
  });
});

describe('черновик целиком', () => {
  const GOOD = { min_dr: 20, min_org_traffic: 500, min_refdomains: 100, min_keywords: 300 };

  it('годный уходит на сервер как есть', () => {
    expect(bodyOf(GOOD, LIMITS)).toEqual(GOOD);
  });

  it('одно негодное поле держит весь черновик: предпросмотр ждёт все четыре', () => {
    expect(bodyOf({ ...GOOD, min_keywords: '' }, LIMITS)).toBeNull();
    expect(bodyOf({ ...GOOD, min_dr: 95 }, LIMITS)).toBeNull();
  });
});
