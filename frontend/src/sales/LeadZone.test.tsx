/**
 * Пояс лида на вкладке «emails»: на виду — смещение от UTC, имя из базы поясов — в подсказке у
 * значения (проверка прода 10.11.2026: имя вида «Europe/Berlin» стояло кодом в каждой строке).
 *
 * Таблица отрисована без экрана вокруг: проверяется строка, а не запросы. Лиды и компании
 * выдуманы. Часы закреплены: смещение — на сегодня, и у пояса с летним временем оно зависит от дня.
 */

import { screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { LeadCard } from '../api/salesTypes';
import { renderWith } from '../test/render';
import { NO_LEAD_FILTERS } from './leadFilters';
import { LeadsTable } from './LeadsTable';

const WINTER = new Date('2026-11-02T10:00:00Z');
const SUMMER = new Date('2026-07-06T10:00:00Z');

function lead(id: number, timezone: string | null): LeadCard {
  return {
    id,
    email: `buyer${id}@shop${id}.example.test`,
    name: null,
    position: null,
    company: `Shop ${id}`,
    host: `shop${id}.example.test`,
    country: 'de',
    timezone,
    language: 'en',
    hypothesis_id: 1,
    hypothesis: 'сайты EN',
    source: 'import',
    status: 'new',
    rejection_reason: null,
    cleaning_note: null,
    verification_status: null,
    created_at: '2026-10-03T10:00:00+00:00',
  };
}

function show(rows: LeadCard[]): void {
  renderWith(
    <LeadsTable
      rows={rows}
      stale={false}
      empty={null}
      refusal={null}
      onReset={() => {}}
      mayWrite
      onEdit={() => {}}
      filters={NO_LEAD_FILTERS}
      search=""
      onSearch={() => {}}
      onFilter={() => {}}
      hypotheses={[]}
      reasons={[]}
    />,
  );
}

/** Строка лида по его адресу — по разметке, а не по ролям: в таблице их бывают сотни. */
function rowOf(id: number): HTMLElement {
  const row = Array.from(document.querySelectorAll<HTMLElement>('table.leadsTable tbody tr')).find(
    (found) => found.textContent?.includes(`buyer${id}@`),
  );
  if (row === undefined) throw new Error(`строки лида №${id} нет`);
  return row;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(WINTER);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('emails: пояс лида — смещением от UTC', () => {
  it('в строке — смещение, имя пояса — в подсказке у значения; имени на виду нет', () => {
    show([
      lead(1, 'Asia/Tashkent'),
      lead(2, 'Asia/Kolkata'),
      lead(3, 'America/Argentina/ComodRivadavia'),
    ]);

    const shown = [1, 2, 3].map((id) => {
      const zone = rowOf(id).querySelector('.leadZone');
      return [zone?.textContent, zone?.getAttribute('title')];
    });
    expect(shown).toEqual([
      ['UTC+5', 'Asia/Tashkent'],
      ['UTC+5:30', 'Asia/Kolkata'],
      ['UTC−3', 'America/Argentina/ComodRivadavia'],
    ]);
    expect(screen.queryByText(/Asia\/|America\//)).toBeNull();
    // Страна — как была, словом над поясом.
    expect(within(rowOf(1)).getByText('Германия')).toBeInTheDocument();
  });

  it('смещение — на сегодня: зимой — зимнее время пояса', () => {
    show([lead(1, 'Europe/Berlin')]);
    expect(rowOf(1).querySelector('.leadZone')).toHaveTextContent('UTC+1');
  });

  it('летом — летнее время того же пояса', () => {
    vi.setSystemTime(SUMMER);
    show([lead(1, 'Europe/Berlin')]);
    expect(rowOf(1).querySelector('.leadZone')).toHaveTextContent('UTC+2');
  });

  it('браузер пояса не знает — имя как есть, одной строкой, целиком в подсказке', () => {
    show([lead(1, 'Mars/Olympus_Mons_Station')]);

    const zone = within(rowOf(1)).getByText('Mars/Olympus_Mons_Station');
    expect(zone).toHaveAttribute('title', 'Mars/Olympus_Mons_Station');
    expect(zone).toHaveAttribute('data-line-clamp');
  });

  it('пояса у лида нет — строки пояса нет', () => {
    show([lead(1, null), lead(2, 'Asia/Tashkent')]);

    expect(rowOf(1).querySelector('.leadZone')).toBeNull();
    expect(rowOf(2).querySelector('.leadZone')).toHaveTextContent('UTC+5');
  });

  it('пояс один у всех строк страницы — часы пояса заводятся один раз, а не на строку', () => {
    const made = vi.spyOn(Intl, 'DateTimeFormat');
    show(Array.from({ length: 40 }, (_, index) => lead(index + 1, 'Asia/Karachi')));

    expect(document.querySelectorAll('.leadZone')).toHaveLength(40);
    expect(rowOf(40).querySelector('.leadZone')).toHaveTextContent('UTC+5');
    const clocks = made.mock.calls.filter(([, options]) => options?.timeZone === 'Asia/Karachi');
    expect(clocks).toHaveLength(1);
  });
});
