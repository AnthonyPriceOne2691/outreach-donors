/**
 * Чей адрес — у пунктов меню одного пути с разной частью: «Диалоги с донорами»
 * (`/threads?stage=donors`), «Диалоги с рекламодателями» и «Диалоги продаж»; «Письма
 * донорам» и «Письма рекламодателям». Подсветка и память места — по ключу пункта.
 */

import { describe, expect, it } from 'vitest';

import { activeKey, sectionKey, SECTIONS } from './sections';
import type { Section } from './sections';

const titled = (title: string): Section => {
  const found = SECTIONS.find((section) => section.title === title);
  if (found === undefined) throw new Error(`пункта «${title}» нет`);
  return found;
};

/** Пункты Этапа 1 и Этапа 2 — части «Писем» и «Диалогов» (23.10.2026). */
const STAGE_ITEMS = [
  'Письма донорам',
  'Диалоги с донорами',
  'Письма рекламодателям',
  'Диалоги с рекламодателями',
];

describe('ключ пункта', () => {
  it('путь, у части — с её параметром', () => {
    expect(sectionKey(titled('Письма донорам'))).toBe('/letters?stage=donors');
    expect(sectionKey(titled('Диалоги с донорами'))).toBe('/threads?stage=donors');
    expect(sectionKey(titled('Письма рекламодателям'))).toBe('/letters?stage=advertisers');
    expect(sectionKey(titled('Диалоги с рекламодателями'))).toBe('/threads?stage=advertisers');
    expect(sectionKey(titled('Диалоги продаж'))).toBe('/threads?stage=sales');
    expect(sectionKey(titled('Агент продаж'))).toBe('/agent?stage=sales');
    expect(sectionKey(titled('Домены продаж'))).toBe('/senders?stage=sales');
  });

  it('у каждого пункта свой: двух пунктов с одним ключом нет', () => {
    const keys = SECTIONS.map(sectionKey);
    expect(new Set(keys).size).toBe(keys.length);
  });
});

describe('значки', () => {
  it('в группе доноров и рекламодателей у каждого пункта свой — свёрнутое меню различимо', () => {
    const icons = SECTIONS.filter((section) => section.group === 'mail').map((one) => one.icon);
    expect(new Set(icons).size).toBe(icons.length);
  });

  it('значков пунктов Этапа 1 и Этапа 2 нет больше ни у кого в меню', () => {
    for (const title of STAGE_ITEMS) {
      const { icon } = titled(title);
      expect(SECTIONS.filter((section) => section.icon === icon)).toHaveLength(1);
    }
  });
});

describe('пункт открытого адреса', () => {
  it('один путь — различает часть в адресе, вместе с прочими параметрами', () => {
    expect(activeKey(SECTIONS, '/threads/42', '?state=lead&stage=advertisers')).toBe(
      '/threads?stage=advertisers',
    );
    expect(activeKey(SECTIONS, '/threads/42', '?stage=donors&state=needs_review')).toBe(
      '/threads?stage=donors',
    );
    expect(activeKey(SECTIONS, '/threads/42', '?stage=sales&state=sales_pending')).toBe(
      '/threads?stage=sales',
    );
    expect(activeKey(SECTIONS, '/letters', '?stage=advertisers')).toBe(
      '/letters?stage=advertisers',
    );
    expect(activeKey(SECTIONS, '/agent', '?stage=sales')).toBe('/agent?stage=sales');
    expect(activeKey(SECTIONS, '/agent', '')).toBe('/agent');
  });

  it('прежние адреса без части — ничьи: пунктов без части у «Писем» и «Диалогов» нет', () => {
    expect(activeKey(SECTIONS, '/threads', '')).toBeNull();
    expect(activeKey(SECTIONS, '/threads/42', '?state=lead')).toBeNull();
    expect(activeKey(SECTIONS, '/letters', '')).toBeNull();
    expect(activeKey(SECTIONS, '/threads', '?stage=elsewhere')).toBeNull();
  });

  it('часть без пункта (нет права, у экрана её нет) — пункт без части, если он есть', () => {
    const withoutSales = SECTIONS.filter((section) => section.group !== 'sales');
    expect(activeKey(withoutSales, '/threads', '?stage=sales')).toBeNull();
    expect(activeKey(withoutSales, '/agent', '?stage=sales')).toBe('/agent');
    expect(activeKey(SECTIONS, '/agent', '?stage=donors')).toBe('/agent');
  });

  it('часть не трогает разделы, у которых её нет, а отчёт прогона — «Прогон»', () => {
    expect(activeKey(SECTIONS, '/sales', '?tab=leads')).toBe('/sales');
    expect(activeKey(SECTIONS, '/runs/27', '')).toBe('/run');
    expect(activeKey(SECTIONS, '/', '')).toBe('/');
    expect(activeKey(SECTIONS, '/nowhere', '')).toBeNull();
  });
});
