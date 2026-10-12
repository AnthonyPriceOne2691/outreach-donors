/**
 * Чей адрес — у пунктов меню одного пути с разной частью: «Диалоги» (`/threads`) и
 * «Диалоги продаж» (`/threads?stage=sales`). Подсветка и память места — по ключу пункта.
 */

import { describe, expect, it } from 'vitest';

import { activeKey, sectionKey, SECTIONS } from './sections';
import type { Section } from './sections';

const titled = (title: string): Section => {
  const found = SECTIONS.find((section) => section.title === title);
  if (found === undefined) throw new Error(`пункта «${title}» нет`);
  return found;
};

describe('ключ пункта', () => {
  it('путь, у части — с её параметром', () => {
    expect(sectionKey(titled('Диалоги'))).toBe('/threads');
    expect(sectionKey(titled('Диалоги продаж'))).toBe('/threads?stage=sales');
    expect(sectionKey(titled('Агент продаж'))).toBe('/agent?stage=sales');
    expect(sectionKey(titled('Домены продаж'))).toBe('/senders?stage=sales');
  });

  it('у каждого пункта свой: двух пунктов с одним ключом нет', () => {
    const keys = SECTIONS.map(sectionKey);
    expect(new Set(keys).size).toBe(keys.length);
  });
});

describe('пункт открытого адреса', () => {
  it('один путь — различает часть в адресе, вместе с прочими параметрами', () => {
    expect(activeKey(SECTIONS, '/threads/42', '?state=lead')).toBe('/threads');
    expect(activeKey(SECTIONS, '/threads/42', '?stage=sales&state=sales_pending')).toBe(
      '/threads?stage=sales',
    );
    expect(activeKey(SECTIONS, '/agent', '?stage=sales')).toBe('/agent?stage=sales');
    expect(activeKey(SECTIONS, '/agent', '')).toBe('/agent');
  });

  it('незнакомая часть и часть без пункта (нет права) — пункт без части', () => {
    expect(activeKey(SECTIONS, '/threads', '?stage=donors')).toBe('/threads');
    const withoutSales = SECTIONS.filter((section) => section.group !== 'sales');
    expect(activeKey(withoutSales, '/threads', '?stage=sales')).toBe('/threads');
  });

  it('часть не трогает разделы, у которых её нет, а отчёт прогона — «Прогон»', () => {
    expect(activeKey(SECTIONS, '/sales', '?tab=leads')).toBe('/sales');
    expect(activeKey(SECTIONS, '/runs/27', '')).toBe('/run');
    expect(activeKey(SECTIONS, '/', '')).toBe('/');
    expect(activeKey(SECTIONS, '/nowhere', '')).toBeNull();
  });
});
