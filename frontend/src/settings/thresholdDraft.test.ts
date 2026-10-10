/**
 * Проверка порога — то же правило, что у схемы сервера: целое, в границах
 * включительно, пустое поле — не ноль, а набранное остаётся как набрано.
 */

import { describe, expect, it } from 'vitest';

import { bodyOf, draftOf, fieldRefusal } from './thresholdDraft';
import type { ThresholdLimits } from './thresholdDraft';

const LIMITS: ThresholdLimits = {
  min_dr: { min: 0, max: 90 },
  min_org_traffic: { min: 0, max: 10_000_000 },
  min_refdomains: { min: 0, max: 1_000_000 },
  min_keywords: { min: 0, max: 1_000_000 },
};

const GOOD = { min_dr: 20, min_org_traffic: 500, min_refdomains: 100, min_keywords: 300 };

describe('поле порога', () => {
  it('сами границы допустимы, шаг за ними — нет', () => {
    expect(fieldRefusal('0', LIMITS.min_dr)).toBeNull();
    expect(fieldRefusal('90', LIMITS.min_dr)).toBeNull();
    expect(fieldRefusal('91', LIMITS.min_dr)).toBe('Допустимо от 0 до 90');
    expect(fieldRefusal('-1', LIMITS.min_dr)).toBe('Допустимо от 0 до 90');
  });

  it('пустое поле просит число, а не становится нулём', () => {
    expect(fieldRefusal('', LIMITS.min_dr)).toBe('Впишите число');
    expect(fieldRefusal('  ', LIMITS.min_dr)).toBe('Впишите число');
  });

  // Числовое поле Mantine молча делало из «2.5» и «2,5» 25, из «1e3» — 13
  // (проверка QA 10.10.2026): DR 20,5 в базе не бывает, и склеенное число — не порог.
  it.each(['2.5', '2,5', '1e3', '20.0'])('«%s» — не целое: отказ, а не другое число', (typed) => {
    expect(fieldRefusal(typed, LIMITS.min_dr)).toBe('Только целое число от 0 до 90');
  });

  it('число длиннее безопасного целого не проходит границы, а не обрезается', () => {
    expect(fieldRefusal('123456789012345678901', LIMITS.min_org_traffic)).toMatch(
      /^Допустимо от 0 до 10/,
    );
  });

  it('разряды в наборе не мешают: «1 000 000» — миллион', () => {
    const draft = { ...draftOf(GOOD), min_org_traffic: '1 000 000' };
    expect(bodyOf(draft, LIMITS)?.min_org_traffic).toBe(1_000_000);
  });
});

describe('черновик целиком', () => {
  it('действующие пороги встают в поля числами по-русски, с разрядами', () => {
    expect(draftOf({ ...GOOD, min_org_traffic: 10_000_000 })).toEqual({
      min_dr: '20',
      min_org_traffic: '10 000 000',
      min_refdomains: '100',
      min_keywords: '300',
    });
  });

  it('годный уходит на сервер числами', () => {
    expect(bodyOf(draftOf(GOOD), LIMITS)).toEqual(GOOD);
  });

  it('одно негодное поле держит весь черновик: предпросмотр ждёт все четыре', () => {
    expect(bodyOf({ ...draftOf(GOOD), min_keywords: '' }, LIMITS)).toBeNull();
    expect(bodyOf({ ...draftOf(GOOD), min_dr: '95' }, LIMITS)).toBeNull();
    expect(bodyOf({ ...draftOf(GOOD), min_dr: '2.5' }, LIMITS)).toBeNull();
  });
});
