/**
 * Журнал действий словами — записи модуля продаж (ревью стыков, круг 2).
 *
 * Модуль продаж пишет в журнал цели, которых нет в словаре экрана: правка лида
 * (`sales_lead:N`), снятая запись стоп-листа продаж (`sales_stoplist:N`), решение по сайту сбора
 * (`sales_segment_candidate:N`). Незнакомое не пропадает — показывается как записано, кодом.
 * Правка лида пишется действием загрузки базы (у лида одна история данных — `sales/lead_edit.py`),
 * и экран называет её «База лидов загружена». Словарь — общий код (`api/auditLabels.ts`): правка —
 * PR «общее» (ревью стыков R7, 7.3).
 */

import { describe, expect, it } from 'vitest';

import { actionTitle, targetParts } from './auditLabels';

const NOBODY = new Map<number, string>();

function words(target: string): string {
  return targetParts(target, NOBODY)
    .map((part) => part.text)
    .join(', ');
}

describe('журнал: записи продаж словами', () => {
  it('опора: цели продаж из словаря — словами (гипотеза, передача)', () => {
    expect(words('sales_hypothesis:3')).toBe('гипотеза продаж №3');
    expect(words('sales_handoff:7')).toBe('передача лида продаж №7');
  });

  it('правка лида, снятая запись стоп-листа и сайт сбора — словами, а не кодом', () => {
    for (const target of ['sales_lead:5', 'sales_stoplist:8', 'sales_segment_candidate:9']) {
      expect(words(target)).not.toMatch(/^sales_/);
      expect(words(target)).toMatch(/№\d+$/);
    }
  });

  it('правка лида — не «База лидов загружена»', () => {
    expect(actionTitle('sales_leads_imported', 'sales_lead:5')).not.toBe('База лидов загружена');
  });
});
