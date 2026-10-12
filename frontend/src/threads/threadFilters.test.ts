/**
 * Часть списка диалогов в адресе (`?stage=sales`): читается только с правом «Продажи»,
 * пишется первой, сбрасывается отдельно от фильтров, а переписка продаж стоит только
 * в своей части.
 */

import { describe, expect, it } from 'vitest';

import type { ThreadCard } from '../api/types';
import { inPart, isThreadFiltered, readThreadFilters, writeThreadFilters } from './threadFilters';

const card = (stage: ThreadCard['stage']) => ({ stage }) as ThreadCard;

describe('часть списка диалогов', () => {
  it('из адреса — с правом «Продажи»; без права и незнакомая — без части', () => {
    const asked = new URLSearchParams('stage=sales&state=sales_pending');
    expect(readThreadFilters(asked, true)).toEqual({
      part: 'sales',
      state: 'sales_pending',
      search: '',
    });
    expect(readThreadFilters(asked, false)).toEqual({ part: null, state: null, search: '' });
    expect(readThreadFilters(new URLSearchParams('stage=donors'), true).part).toBeNull();
  });

  it('в адрес — первой, а «найдено N из M» её не считает: часть — сам список', () => {
    const filters = { part: 'sales' as const, state: null, search: 'acme' };
    expect(writeThreadFilters(filters).toString()).toBe('stage=sales&search=acme');
    expect(isThreadFiltered({ ...filters, search: '' })).toBe(false);
  });

  it('переписка продаж — только в части продаж, доноры и рекламодатели — без неё', () => {
    expect(inPart(card('sales'), 'sales')).toBe(true);
    expect(inPart(card('sales'), null)).toBe(false);
    expect(inPart(card('donors'), null)).toBe(true);
    expect(inPart(card('advertisers'), null)).toBe(true);
    expect(inPart(card('advertisers'), 'sales')).toBe(false);
  });
});
