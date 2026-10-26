/**
 * Часть списка диалогов в адресе (`?stage=…`): продажи читаются только с правом «Продажи»,
 * доноры и рекламодатели (23.10.2026) — у всех; часть пишется первой, сбрасывается отдельно
 * от фильтров, а переписка стоит только в части своего этапа.
 *
 * Кому писали (`?role=`, слово владельца 14.10.2026): доноры или рекламодатели — фильтр
 * «Диалогов»; в части продаж он не сужает.
 */

import { describe, expect, it } from 'vitest';

import type { ThreadCard } from '../api/types';
import {
  inPart,
  isThreadFiltered,
  NO_THREAD_FILTERS,
  readThreadFilters,
  threadEmptiness,
  threadMatches,
  writeThreadFilters,
} from './threadFilters';

const card = (stage: ThreadCard['stage']) => ({ stage }) as ThreadCard;

describe('часть списка диалогов', () => {
  it('из адреса — с правом «Продажи»; без права и незнакомая — без части', () => {
    const asked = new URLSearchParams('stage=sales&state=sales_pending');
    expect(readThreadFilters(asked, true)).toEqual({
      part: 'sales',
      role: null,
      state: 'sales_pending',
      search: '',
    });
    expect(readThreadFilters(asked, false)).toEqual({
      part: null,
      role: null,
      state: null,
      search: '',
    });
    expect(readThreadFilters(new URLSearchParams('stage=elsewhere'), true).part).toBeNull();
  });

  it('доноры и рекламодатели — части и без права «Продажи»; «Кому писали» в части не сужает', () => {
    expect(readThreadFilters(new URLSearchParams('stage=donors'), false).part).toBe('donors');
    const advertisers = readThreadFilters(
      new URLSearchParams('stage=advertisers&role=donors'),
      false,
    );
    expect(advertisers).toEqual({ part: 'advertisers', role: null, state: null, search: '' });
  });

  it('в адрес — первой, а «найдено N из M» её не считает: часть — сам список', () => {
    const filters = { part: 'sales' as const, role: null, state: null, search: 'acme' };
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

  it('часть доноров — только доноры, часть рекламодателей — только рекламодатели', () => {
    expect(inPart(card('donors'), 'donors')).toBe(true);
    expect(inPart(card('advertisers'), 'donors')).toBe(false);
    expect(inPart(card('sales'), 'donors')).toBe(false);
    expect(inPart(card('advertisers'), 'advertisers')).toBe(true);
    expect(inPart(card('donors'), 'advertisers')).toBe(false);
  });

  it('пустая часть называет экран писем, где заводится её переписка', () => {
    const empty = (part: 'donors' | 'advertisers' | null) =>
      threadEmptiness({ ...NO_THREAD_FILTERS, part }, new Map(), 0).detail;
    expect(empty('donors')).toContain('«Письма донорам»');
    expect(empty('advertisers')).toContain('«Письма рекламодателям»');
    expect(empty(null)).toContain('«Письма донорам» и «Письма рекламодателям»');
  });
});

describe('кому писали: доноры или рекламодатели', () => {
  it('из адреса — в «Диалогах»; в части продаж и незнакомое — не сужать', () => {
    expect(readThreadFilters(new URLSearchParams('role=advertisers'), true).role).toBe(
      'advertisers',
    );
    expect(readThreadFilters(new URLSearchParams('role=donors'), false).role).toBe('donors');
    expect(readThreadFilters(new URLSearchParams('stage=sales&role=donors'), true).role).toBeNull();
    expect(readThreadFilters(new URLSearchParams('role=sales'), true).role).toBeNull();
  });

  it('в адрес — после части; это фильтр: «найдено N из M» и сброс его видят', () => {
    const filters = { ...NO_THREAD_FILTERS, role: 'advertisers' as const, search: 'acme' };
    expect(writeThreadFilters(filters).toString()).toBe('role=advertisers&search=acme');
    expect(isThreadFiltered({ ...NO_THREAD_FILTERS, role: 'donors' })).toBe(true);
  });

  it('рекламодатели — только переписки рекламодателей, доноры — только доноров', () => {
    const advertisers = { ...NO_THREAD_FILTERS, role: 'advertisers' as const };
    const donors = { ...NO_THREAD_FILTERS, role: 'donors' as const };
    expect(threadMatches(card('advertisers'), advertisers)).toBe(true);
    expect(threadMatches(card('donors'), advertisers)).toBe(false);
    expect(threadMatches(card('donors'), donors)).toBe(true);
    expect(threadMatches(card('advertisers'), NO_THREAD_FILTERS)).toBe(true);
  });

  it('пустой список под переключателем называет его словами', () => {
    const empty = threadEmptiness({ ...NO_THREAD_FILTERS, role: 'advertisers' }, new Map(), 3);
    expect(empty).toEqual({
      title: 'Под фильтр ничего не попало.',
      detail: 'Условия: только рекламодатели. Всего диалогов — 3.',
    });
  });
});
