/**
 * Роль «Рекламодатели» на экранах: Этап 2 — целиком, Этапа 1, продаж и «Расхода» нет.
 *
 * Зеркало роли «Аутрич» (`DonorsRole.test.tsx`), и не только по меню: общие экраны открывают её
 * этап сами, а не падают на этапе доноров, который сервер ей не отдаст. Запрос на незаписанный
 * адрес роняет тест сам (`test/server.ts`); записанный запрос Этапа 1 ловит `closedCalls`.
 */

import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { OverviewView } from '../api/types';
import { ADVERTISERS_ONLY, OVERVIEW, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Recorded } from '../test/server';

const LETTERS_SENT = { queued: 29, sent: 141, delivered: 129, bounced: 12 };

/** Сводка так, как её отдаёт сервер без права «Доноры»: этапа в письмах нет, чисел Этапа 1 — тоже,
 *  а формы — только рекламодателей. */
const ONLY_ADVERTISERS_OVERVIEW: OverviewView = {
  ...OVERVIEW,
  last_run: null,
  letters: { advertisers: LETTERS_SENT },
  waiting: { ...OVERVIEW.waiting, forms: 12, advertiser_forms: 12 },
};

const LETTERS = {
  letters: [],
  queued_total: 0,
  batch_max: 200,
  followup_default: [7, 14],
  letter_default: { subject: 'Тема', zones: [] },
  blocked_by: [],
  transport: { name: 'null', real: false, problem: null },
  corridor: { min: 0.15, max: 0.25 },
  funnel: {},
};

const QUEUE_LINE = { stage: 'advertisers', letters: [], after_minutes: 5, total: 0 };

/** Адреса Этапа 1 и «Расхода»: их экран роли не спрашивает, даже если ответ записан. */
const CLOSED =
  /^\/api\/(runs|keywords|selection|review|price-rechecks|contacts|usage|settings\/thresholds|replies\/calibration)|^\/api\/donors(?!\/import)/;

function closedCalls({ calls }: Recorded): string[] {
  return calls
    .map((call) => call.path)
    .filter((path) => CLOSED.test(path) || path.includes('stage=donors'));
}

function open(path: string, routes: Record<string, unknown> = {}): Recorded {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADVERTISERS_ONLY },
    'GET /api/watchdog': { body: { alarms: [] } },
    'GET /api/overview': { body: ONLY_ADVERTISERS_OVERVIEW },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, path);
  return recorded;
}

const menu = async () => within(await screen.findByRole('navigation', { name: 'Разделы' }));

describe('меню и «Обзор» роли «Рекламодатели»', () => {
  it('группы «Доноры» нет, «Обзор» и «Формы» — в её группе, «Расхода» нет', async () => {
    const recorded = open('/');

    const items = await menu();
    const own = within(await items.findByRole('group', { name: 'Рекламодатели' }));
    expect(items.queryByRole('group', { name: 'Доноры' })).not.toBeInTheDocument();
    expect(items.queryByRole('group', { name: 'Продажи' })).not.toBeInTheDocument();
    for (const name of [
      'Обзор',
      'Формы',
      'Рекламодатели',
      'Письма рекламодателям',
      'Диалоги с рекламодателями',
    ]) {
      expect(own.getByRole('link', { name: new RegExp(`^${name}`) })).toBeInTheDocument();
    }
    for (const name of [/^Прогон/, /^Доноры/, /^Отбор/, /^Расход/, /донорам/, /с донорами/]) {
      expect(items.queryByRole('link', { name })).not.toBeInTheDocument();
    }
    expect(closedCalls(recorded)).toEqual([]);
  });

  it('«Обзор»: плитки и письма Этапа 2, воронки, писем донорам и прогонов нет', async () => {
    const recorded = open('/');

    expect(await screen.findByText('Лиды рекламодателей')).toBeInTheDocument();
    expect(screen.getByText('Кандидаты в рекламодатели')).toBeInTheDocument();
    // Формы — только этапа роли: без частей, донорской «доноров 0» у неё нет (живая проверка 03.11).
    const forms = screen.getByText('Заполнить формы').closest('a');
    expect(forms).not.toBeNull();
    expect(within(forms as HTMLElement).getByText('12')).toBeInTheDocument();
    expect(
      within(forms as HTMLElement).getByText('у сайта форма вместо адреса'),
    ).toBeInTheDocument();
    expect(within(forms as HTMLElement).queryByText(/доноров/)).not.toBeInTheDocument();
    expect(screen.getByText('Письма рекламодателям')).toBeInTheDocument();
    for (const gone of [
      'Рассмотреть домены',
      'Разобрать цены',
      'Воронка доноров',
      'Письма донорам',
      'Прогон и расход',
    ]) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument();
    }
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(closedCalls(recorded)).toEqual([]);
  });

  it.each([
    ['/run', /Действие «Доноры» не выдано/],
    ['/donors', /Действие «Доноры» не выдано/],
    ['/donors/7', /Действие «Доноры» не выдано/],
    ['/selection', /Действие «Доноры» не выдано/],
    ['/runs/3', /Действие «Доноры» не выдано/],
    ['/usage', /Действие «смотреть расход» не выдано/],
  ])('%s объясняет, какого права нет, и ничего не спрашивает', async (path, words) => {
    const recorded = open(path);

    expect(await screen.findByText('Раздел недоступен')).toBeInTheDocument();
    expect(screen.getByText(words)).toBeInTheDocument();
    expect(closedCalls(recorded)).toEqual([]);
  });
});

describe('общие экраны открывают этап роли', () => {
  it.each(['/letters', '/letters?stage=donors'])(
    '%s — «Письма рекламодателям», запрос этапа роли',
    async (path) => {
      const recorded = open(path, {
        'GET /api/letters?stage=advertisers': {
          body: { ...LETTERS, stage: 'advertisers', audience: 'links' },
        },
        'GET /api/letters/unknown?stage=advertisers': { body: QUEUE_LINE },
        'GET /api/letters/deferred?stage=advertisers': { body: QUEUE_LINE },
      });

      expect(
        await screen.findByRole('heading', { name: 'Письма рекламодателям' }),
      ).toBeInTheDocument();
      expect(screen.queryByText('Очередь не загрузилась')).not.toBeInTheDocument();
      expect(closedCalls(recorded)).toEqual([]);
    },
  );

  it('формы: очередь рекламодателей без вкладок, очередь доноров не спрашивается', async () => {
    const recorded = open('/forms', {
      'GET /api/advertisers/forms?page=1': {
        body: { rows: [], total: 0, page: 1, limit: 20, monthly_left: 9, monthly_cap: 10 },
      },
    });

    expect(await screen.findByText(/Очередь пуста/)).toBeInTheDocument();
    expect(screen.queryByRole('radiogroup', { name: 'Чья очередь форм' })).not.toBeInTheDocument();
    expect(closedCalls(recorded)).toEqual([]);
  });

  it('диалоги: часть рекламодателей, адрес доноров её не открывает', async () => {
    const recorded = open('/threads?stage=donors', {
      'GET /api/threads': { body: [] },
      'GET /api/replies/unbound?page=1': { body: { rows: [], total: 0, page: 1, limit: 20 } },
    });

    expect(
      await screen.findByRole('heading', { name: 'Диалоги с рекламодателями' }),
    ).toBeInTheDocument();
    expect(closedCalls(recorded)).toEqual([]);
  });

  it('«Пороги»: порогов отбора нет, их запроса тоже', async () => {
    const recorded = open('/settings');

    expect(await screen.findByRole('heading', { name: 'Пороги' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Пороги отбора' })).not.toBeInTheDocument();
    expect(closedCalls(recorded)).toEqual([]);
  });
});
