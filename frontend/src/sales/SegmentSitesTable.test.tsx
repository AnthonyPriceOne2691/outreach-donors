/**
 * Раскладка таблицы сайтов прогона: во что она умещается и что обрезается.
 *
 * Замеры стенда (1440 и 390 px): на 1440 при раскрытом меню таблице доставалось около 1 044 px —
 * шире, и «Итог» или «Решение» уезжали в прокрутку; теперь таблица — в окне «Сайты прогона»
 * (на 1440 ему 1 280 px, таблице в нём — 1 240, замер стенда 20.10). Предел — ширина таблицы в окне:
 * бейдж «судья отказал бы» шире прежнего запаса в 1 044 px на «Все» и на вкладке решения. На телефоне строка — карточкой, и длинное
 * в ней — целиком: наведения там нет, подсказка по наведению не открылась бы. На «На проверку»
 * причина целиком и в таблице: по ней решают; решённая строка — словами на месте кнопок.
 */

import { MantineProvider } from '@mantine/core';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { theme } from '../theme';
import type { SegmentSite, SegmentSitesPage, SiteTab } from './SegmentSitesApi';
import type { Held } from './SegmentSitesDecide';
import { columnsOf, minWidthOf, SegmentSitesTable } from './SegmentSitesTable';
import { sitesPoll } from './SegmentSites';
import type { SegmentRunRow } from './segmentApi';

/** Ширина таблицы в окне «Сайты прогона» на 1440 — замер стенда 20.10. */
const WINDOW_1440 = 1240;
const TABS: SiteTab[] = ['all', 'accept', 'review', 'would_reject', 'reject', 'pending', 'changed'];
/** Порог карточек — тот же, что у `phoneCards` в `styles/glass.css`. */
const PHONE = '(max-width: 47.99em)';

const REASON =
  'Отказ: справочник — списки чужих мастерских по городам, кнопка «добавить компанию» и отзывы, ' +
  'своей мебели не делает';
const QUOTE = 'Alle Möbeltischler in Ihrer Stadt finden und vergleichen';
const OUTCOME = 'адрес ещё не искали — найдёт продолжение прогона';

const site: SegmentSite = {
  id: 5,
  host: 'branchenbuch-moebel.example.test',
  page_url: 'https://branchenbuch-moebel.example.test/',
  page_title: 'Branchenbuch',
  keyword: 'möbel nach maß',
  lane: 'accept',
  verdict: 'reject',
  decided_by: 'model',
  decided_by_user: null,
  decided_at: '2026-10-12T08:05:00+00:00',
  reason: REASON,
  quote: QUOTE,
  unanswered: false,
  model: 'gpt-5-mini',
  undoable: false,
  judge_reason: null,
  outcome: {
    kind: 'not_searched',
    words: OUTCOME,
    lead_id: null,
    lead_status: null,
    lead_reason: null,
  },
  change: null,
};

const page: SegmentSitesPage = {
  run_id: 21,
  lane: 'all',
  decider: null,
  page: 1,
  limit: 50,
  total: 1,
  lanes: { all: 1, accept: 1, review: 0, would_reject: 0, reject: 0, pending: 0, changed: 0 },
  deciders: { rule: 0, model: 1, language: 0, human: 0, none: 0 },
  rows: [site],
};

/** Строки «на проверку»: решают по причине — она целиком и в таблице. */
const reviewed: SegmentSite = { ...site, id: 6, lane: 'review', outcome: null };
const second: SegmentSite = {
  ...site,
  id: 7,
  host: 'escuela-idiomas-sample.example.test',
  lane: 'review',
  reason: 'по тексту не понять: школа это или блог преподавателя',
  quote: 'Aprende inglés con nuestros grupos reducidos',
  outcome: null,
};

function screenOf(
  phone: boolean,
  {
    tab = 'all',
    held = {},
    rows,
  }: { tab?: SiteTab; held?: Record<number, Held>; rows?: SegmentSite[] } = {},
) {
  // Заглушку снимает `restoreAllMocks` после теста (`test/setup.ts`).
  vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
    matches: phone && query === PHONE,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
  render(
    <MantineProvider theme={theme}>
      <MemoryRouter>
        <SegmentSitesTable
          tab={tab}
          page={
            tab === 'review'
              ? { ...page, lane: 'review', rows: rows ?? [reviewed, second] }
              : { ...page, rows: rows ?? page.rows }
          }
          decider={null}
          onDecider={() => {}}
          held={held}
          stale={false}
          onAct={() => {}}
        />
      </MemoryRouter>
    </MantineProvider>,
  );
}

describe('таблица сайтов прогона', () => {
  it.each(TABS)('вкладка «%s» умещается в окно на 1440 без прокрутки', (tab) => {
    expect(minWidthOf(columnsOf(tab))).toBeLessThanOrEqual(WINDOW_1440);
  });

  it('полоса — по самому длинному бейджу вкладки: «ещё не судили», «судья отказал бы»', () => {
    const lane = (tab: SiteTab) => columnsOf(tab)[1]?.width;

    expect(lane('pending')).toBe('9.5rem');
    expect([lane('all'), lane('would_reject'), lane('changed')]).toEqual([
      '10.5rem',
      '10.5rem',
      '10.5rem',
    ]);
    expect([lane('review'), lane('accept'), lane('reject')]).toEqual([
      '8.5rem',
      '8.5rem',
      '8.5rem',
    ]);
  });

  it('решение — у полос, где оно за человеком: «На проверку» и «Судья отказал бы»', () => {
    const titles = (tab: SiteTab) => columnsOf(tab).map((column) => column.title);

    expect(titles('review')).toContain('Решение');
    expect(titles('would_reject')).toContain('Решение');
    expect(titles('all')).not.toContain('Решение');
  });

  it('в таблице длинное — двумя строками, целиком — в подсказке', () => {
    screenOf(false);

    expect(screen.getByText(REASON)).toHaveAttribute('data-line-clamp', 'true');
    expect(screen.getByText(REASON)).toHaveAttribute('title', REASON);
    expect(screen.getByText(OUTCOME)).toHaveAttribute('data-line-clamp', 'true');
  });

  it('на «На проверку» причина — целиком: по ней решают; цитата — двумя строками', () => {
    screenOf(false, { tab: 'review' });

    const [reason] = screen.getAllByText(REASON);
    expect(reason).not.toHaveAttribute('data-line-clamp');
    expect(screen.getAllByText(`«${QUOTE}»`)[0]).toHaveAttribute('data-line-clamp', 'true');
  });

  it('решённая строка — словами на месте кнопок: «записываем…», потом «решено»', () => {
    // Строка, записанная сервером, — его ответ на месте прежней (`SegmentSites.rowsOf`).
    const written: SegmentSite = {
      ...second,
      lane: 'reject',
      decided_by: 'human',
      decided_by_user: 'seller@ours.example.test',
      reason: 'отказал человек',
      undoable: true,
      judge_reason: second.reason,
    };
    screenOf(false, {
      tab: 'review',
      rows: [reviewed, written],
      held: { 6: { site: reviewed, doing: 'accept' }, 7: { site: written, doing: null } },
    });

    // Строки тела таблицы: над ними — подписи колонок и строка фильтров.
    const [first, next] = screen.getAllByRole('row').filter((row) => row.closest('tbody'));
    expect(within(first!).getByText('принят — записываем…')).toBeInTheDocument();
    expect(within(first!).queryByRole('button')).toBeNull();
    expect(within(next!).getByText('решено: отсечён')).toBeInTheDocument();
    expect(within(next!).getByRole('button', { name: 'Отменить решение' })).toBeEnabled();
    // Записано сервером — полоса уже новая; пока пишется — прежняя.
    expect(within(next!).getByText('отсечён', { selector: '.mantine-Badge-label' }));
    expect(within(first!).getByText('на проверке', { selector: '.mantine-Badge-label' }));
  });

  it('на телефоне карточка показывает причину, цитату и итог целиком', () => {
    screenOf(true);

    for (const text of [REASON, `«${QUOTE}»`, OUTCOME]) {
      expect(screen.getByText(text)).not.toHaveAttribute('data-line-clamp', 'true');
    }
    expect(screen.getByText('по ключу «möbel nach maß»')).not.toHaveAttribute('data-truncate');
  });
});

describe('опрос списка сайтов', () => {
  const row: SegmentRunRow = {
    id: 21,
    created_at: '2026-10-12T08:00:00+00:00',
    finished_at: null,
    created_by: 'seller@ours.example.test',
    country: 'de',
    language: 'en',
    depth_pages: 2,
    keywords: 5,
    mode: 'enforce',
    stage: 'done',
    error: null,
    job_id: null,
  };

  it('идёт ли прогон — по очереди задач, как у хода прогона, а не по стадии строки', () => {
    // Готовый прогон продолжили («Найти адреса принятым»): стадия — «готово», а проход идёт.
    expect(sitesPoll({ ...row, stage: 'done', active: true })).toBe(5000);
    // Задача умерла посреди судьи: стадия — «судья», а прогон стоит — опрашивать нечего.
    expect(sitesPoll({ ...row, stage: 'judge', active: false })).toBe(false);
    // Сервер старше хода прогона: поля `active` нет — по стадии.
    expect(sitesPoll({ ...row, stage: 'leads' })).toBe(5000);
    expect(sitesPoll({ ...row, stage: 'done' })).toBe(false);
  });
});
