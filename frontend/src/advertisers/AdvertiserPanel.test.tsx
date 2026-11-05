/**
 * Панель рекламодателей: вид по умолчанию, выборка в адресе, шаги словами сервера, качество
 * загрузок со списком доменов, блоки рассылки и выгрузка спорных файлом.
 *
 * Проверяется то, чем экран может соврать: другая выборка в запросе, чем в адресе, прочерк вместо
 * нуля у доли, партия без выбора с числами «всего времени», файл — по содержимому ответа.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type {
  PanelMailing,
  PanelView,
  QualityHostsView,
  QualityRow,
  QualityView,
} from '../api/advertiserPanel';
import type { AdvertiserFacets } from '../api/advertiserList';
import type { QueueAutosend } from '../api/autosend';
import { FORECAST } from '../sales/mailingFixtures';
import { ADMIN, CRAWL_ROUTES, reviewQueue, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';

const LOAD = {
  id: 7,
  at: '2026-11-02T09:00:00Z',
  kind: 'bulk' as const,
  file_name: 'party.xlsx',
  hosts: 100,
};

const PANEL: PanelView = {
  range: 'today',
  since: '2026-11-01T21:00:00Z',
  load: null,
  loads: [LOAD],
  donors: { added: 100, started: 90, running: 3, waiting: 5, refused: 2, idle: 0 },
  advertisers: {
    found: 40,
    fresh: 30,
    repeat: 10,
    promoted: 35,
    with_address: 20,
    sent: 12,
    delivered: 9,
  },
};

const FILE_ROW: QualityRow = {
  at: '2026-11-02T09:00:00Z',
  kind: 'bulk',
  load_id: 7,
  day: null,
  file_name: 'party.xlsx',
  donors: 100,
  host: null,
  scanned: 90,
  pages: 45_000,
  selling_links: 800,
  selling_pages: 450,
  share: 0.01,
};

const HAND_ROW: QualityRow = {
  ...FILE_ROW,
  at: '2026-11-01T09:00:00Z',
  kind: 'manual',
  load_id: 6,
  file_name: null,
  donors: 1,
  host: 'hand.example.test',
  scanned: 1,
  pages: 10,
  selling_links: null,
  selling_pages: 0,
  share: null,
};

const DAY_ROW: QualityRow = {
  ...FILE_ROW,
  at: '2026-10-31T09:00:00Z',
  kind: 'crawl',
  load_id: null,
  day: '2026-10-31',
  file_name: null,
  donors: 3,
  host: null,
};

const QUALITY: QualityView = {
  rows: [FILE_ROW, HAND_ROW, DAY_ROW],
  total: 3,
  page: 1,
  limit: 20,
};

const HOSTS: QualityHostsView = {
  rows: [{ host: 'one.example.test', crawl: null, selling_links: 4 }],
  total: 1,
  page: 1,
  limit: 50,
};

const QUIET: QueueAutosend = {
  available: true,
  running: false,
  since: null,
  started_by: null,
  sent: 0,
  state: null,
  passed_at: null,
  off: null,
  stopped_at: null,
  stopped_by: null,
  stop_reason: null,
};

const MODES: PanelMailing = { links: QUIET, niche: QUIET };

const FACETS: AdvertiserFacets = {
  sources: { links: 5 },
  addresses: { found: 3, form: 1, none: 1 },
  letters: { sent: 3, queued: 2 },
  donors: {},
};

const FORECAST_ROUTE = 'GET /api/letters/forecast?stage=advertisers&days=7&back=7';

function routes(extra: Record<string, unknown> = {}) {
  return {
    ...CRAWL_ROUTES,
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/advertisers/panel?range=today': { body: PANEL },
    'GET /api/advertisers/panel/quality?page=1': { body: QUALITY },
    'GET /api/advertisers/panel/mailing': { body: MODES },
    'GET /api/advertisers/list/facets': { body: FACETS },
    [FORECAST_ROUTE]: { body: { ...FORECAST, stage: 'advertisers' } },
    ...extra,
  };
}

function open(extra: Record<string, unknown> = {}, path = '/advertisers') {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve(routes(extra));
  renderWith(<AppRoutes />, path);
  return recorded;
}

/** Текст файла. Два разных `Blob` у двух реализаций: из Node его читает `text()`, из jsdom —
 *  `FileReader` (как в `donors/DonorsPage.test.tsx`). */
function textOf(file: Blob): Promise<string> {
  if (typeof file.text === 'function') return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () =>
      typeof reader.result === 'string'
        ? resolve(reader.result)
        : reject(new Error('файл прочитан не текстом'));
    reader.onerror = () => reject(new Error('файл не прочитался'));
    reader.readAsText(file);
  });
}

function panelCalls(recorded: { calls: Call[] }): string[] {
  return recorded.calls
    .map((call: Call) => call.path)
    .filter((url: string) => /^\/api\/advertisers\/panel\?/.test(url));
}

describe('панель рекламодателей', () => {
  it('открывается первой и показывает шаги выборки словами сервера', async () => {
    open();

    expect(await screen.findByRole('heading', { name: 'Панель рекламодателей' })).toBeVisible();
    const tile = (title: string) => screen.getByText(title).closest('.metricTile') as HTMLElement;
    await screen.findByText('Доноров поставлено');
    expect(within(tile('Доноров поставлено')).getByText('100')).toBeVisible();
    expect(within(tile('Доноров поставлено')).getByText('ждут очереди 5')).toBeVisible();
    expect(within(tile('Обход начался')).getByText('идёт 3 · не открылись 2')).toBeVisible();
    expect(
      within(tile('Рекламодателей получено')).getByText('новых 30 · повторных 10'),
    ).toBeVisible();
    expect(within(tile('С адресом')).getByText('переведено 35')).toBeVisible();
    expect(within(tile('Письмо ушло')).getByText('12')).toBeVisible();
    expect(within(tile('Письмо дошло')).getByText('9')).toBeVisible();
  });

  it('проверка открывается своим адресом, а переход на панель чистит его', async () => {
    open(
      {
        'GET /api/advertisers/niche?page=1': {
          body: { rows: [], waiting: 0, total: 0, page: 1, limit: 20 },
        },
        'GET /api/advertisers': { body: reviewQueue() },
      },
      '/advertisers?view=review',
    );
    const user = userEvent.setup();

    expect(await screen.findByText(/Спорных нет/)).toBeVisible();

    await user.click(screen.getByText('Панель'));

    expect(await screen.findByRole('heading', { name: 'Панель рекламодателей' })).toBeVisible();
  });

  it('выборка — в адресе и в запросе', async () => {
    const recorded = open({
      'GET /api/advertisers/panel?range=week': {
        body: { ...PANEL, range: 'week', donors: { ...PANEL.donors, added: 777 } },
      },
    });
    const user = userEvent.setup();
    await screen.findByText('Доноров поставлено');

    await user.click(screen.getByText('Неделя'));

    expect(await screen.findByText('777')).toBeVisible();
    expect(panelCalls(recorded)).toContain('/api/advertisers/panel?range=week');
  });

  it('партия: пока не выбрана, чисел «всего времени» нет; выбранная — своим запросом', async () => {
    const recorded = open({
      'GET /api/advertisers/panel?range=all': { body: PANEL },
      'GET /api/advertisers/panel?range=load&load=7': {
        body: { ...PANEL, range: 'load', load: LOAD, donors: { ...PANEL.donors, added: 321 } },
      },
    });
    const user = userEvent.setup();
    await screen.findByText('Доноров поставлено');

    await user.click(screen.getByText('Партия'));

    const pick = await screen.findByRole('textbox', { name: 'Какая партия' });
    expect(screen.queryByText('Доноров поставлено')).toBeNull();

    await user.click(pick);
    await user.click(await screen.findByRole('option', { name: /party\.xlsx · 100/ }));

    expect(await screen.findByText('321')).toBeVisible();
    expect(panelCalls(recorded)).toContain('/api/advertisers/panel?range=load&load=7');
  });

  it('партий нет — так и сказано, а не пустой список', async () => {
    open(
      {
        'GET /api/advertisers/panel?range=all': { body: { ...PANEL, loads: [] } },
      },
      '/advertisers?range=load',
    );

    expect(await screen.findByText(/Партий пока нет/)).toBeVisible();
  });

  it('отказ панели — словами сервера, а таблица качества живёт', async () => {
    open({
      'GET /api/advertisers/panel?range=today': {
        status: 503,
        body: { detail: 'База не ответила' },
      },
    });

    expect(await screen.findByText('Панель не загрузилась')).toBeVisible();
    expect(await screen.findByText('файлом: party.xlsx')).toBeVisible();
  });
});

describe('качество загрузок', () => {
  it('строка говорит способ, итог и долю; у числа, которого нет, — прочерк', async () => {
    open();

    const file = (await screen.findByText('файлом: party.xlsx')).closest('tr') as HTMLElement;
    expect(within(file).getByText('45 000')).toBeVisible();
    expect(within(file).getByText('800')).toBeVisible();
    expect(within(file).getByText('1%')).toBeVisible();

    const hand = screen.getByText('вручную: hand.example.test').closest('tr') as HTMLElement;
    // Чисел нет — прочерки: ноль сказал бы «искали и не нашли».
    expect(within(hand).getAllByText('—')).toHaveLength(2);
    expect(within(hand).queryByRole('button')).toBeNull();

    const day = screen.getByText('обход без загрузки').closest('tr') as HTMLElement;
    expect(within(day).getByText('31.10.2026')).toBeVisible();
  });

  it('все домены партии — окном, по её номеру', async () => {
    const recorded = open({
      'GET /api/advertisers/panel/quality/hosts?load=7&page=1': { body: HOSTS },
    });
    const user = userEvent.setup();

    await user.click(
      await screen.findByRole('button', { name: 'Домены строки: файлом: party.xlsx' }),
    );

    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText('обхода не было')).toBeVisible();
    expect(within(dialog).getByText('4')).toBeVisible();
    expect(recorded.calls.map((call: Call) => call.path)).toContain(
      '/api/advertisers/panel/quality/hosts?load=7&page=1',
    );
  });

  it('домены обходов без загрузки — по дню', async () => {
    const recorded = open({
      'GET /api/advertisers/panel/quality/hosts?day=2026-10-31&page=1': { body: HOSTS },
    });
    const user = userEvent.setup();

    await user.click(
      await screen.findByRole('button', { name: 'Домены строки: обход без загрузки' }),
    );

    expect(await screen.findByRole('dialog')).toBeVisible();
    await waitFor(() =>
      expect(recorded.calls.map((call: Call) => call.path)).toContain(
        '/api/advertisers/panel/quality/hosts?day=2026-10-31&page=1',
      ),
    );
  });

  it('страница — в адресе и не трогает выборку', async () => {
    const recorded = open(
      {
        'GET /api/advertisers/panel?range=week': { body: { ...PANEL, range: 'week' } },
        'GET /api/advertisers/panel/quality?page=2': {
          body: { ...QUALITY, page: 2, total: 40, rows: [HAND_ROW] },
        },
      },
      '/advertisers?range=week&quality_page=2',
    );

    expect(await screen.findByText('вручную: hand.example.test')).toBeVisible();
    expect(panelCalls(recorded)).toContain('/api/advertisers/panel?range=week');
  });

  it('пустая таблица объясняет, почему пуста', async () => {
    open({
      'GET /api/advertisers/panel/quality?page=1': { body: { ...QUALITY, rows: [], total: 0 } },
    });

    expect(await screen.findByText(/Загрузок и обходов ещё не было/)).toBeVisible();
  });
});

describe('рассылка на панели', () => {
  it('показывает прогноз этапа и части по слову оффера; ящики — на своём экране', async () => {
    open();

    expect(await screen.findByRole('heading', { name: 'Рассылка рекламодателям' })).toBeVisible();
    expect(await screen.findByRole('region', { name: 'Когда уйдёт очередь' })).toBeVisible();
    expect(await screen.findByRole('region', { name: 'Факт за 7 дней' })).toBeVisible();
    const parts = await screen.findByRole('region', { name: 'Рекламодатели по письмам' });
    expect(within(parts).getByText('Ушло')).toBeVisible();
    expect(within(parts).getByText('Собрано')).toBeVisible();
    // Части с нулём не показываются: «снято 0» ни о чём не говорит.
    expect(within(parts).queryByText('Снято')).toBeNull();
    expect(screen.queryByRole('region', { name: 'Ящики и разгон' })).toBeNull();
  });

  it('план на 7 дней — со словами рекламодателей, а не продаж', async () => {
    open();

    expect(await screen.findByRole('region', { name: 'План на 7 дней' })).toBeVisible();
    await userEvent
      .setup()
      .hover(screen.getByRole('button', { name: 'Как считается «План на 7 дней»' }));
    // Подсказка ещё в переходе появления — читаем её текст, а не видимость (как
    // `sales/MailingDays.test`). Только подсказку: причины в строках дня — слова сервера.
    const first = await screen.findByText(/^Мощность ящиков — /);
    const hint = first.parentElement?.textContent ?? '';
    expect(hint).toContain('могут отправить ящики рекламодателей в этот день');
    expect(hint).toContain('не больше, чем есть: письма в очереди рекламодателям.');
    expect(hint).not.toMatch(/ящики продаж|лиды, которым сборка напишет/);
  });

  it('очередь сама не уходит — так и сказано', async () => {
    open();

    expect(await screen.findByText(/Очередь сама не уходит/)).toBeVisible();
  });

  it('у очереди, которая уходит сама, — строка экрана писем', async () => {
    open({
      'GET /api/advertisers/panel/mailing': {
        body: {
          links: {
            ...QUIET,
            running: true,
            since: '2026-11-02T09:00:00Z',
            started_by: 'anna@site.com',
            sent: 12,
          },
          niche: QUIET,
        },
      },
      'GET /api/letters/forecast?stage=advertisers&days=31&back=0': {
        body: { queue_eta: { day: null, sending_days: null, hours: null, words: 'очередь пуста' } },
      },
    });

    expect(await screen.findByText(/Очередь уходит сама с/)).toBeVisible();
    expect(screen.queryByText(/Очередь сама не уходит/)).toBeNull();
  });

  it('отказ прогноза — в его блоке, части и режим живут', async () => {
    open({ [FORECAST_ROUTE]: { status: 503, body: { detail: 'Прогноз не ответил' } } });

    expect(await screen.findByText('Прогноз рассылки не загрузился')).toBeVisible();
    expect(await screen.findByRole('region', { name: 'Рекламодатели по письмам' })).toBeVisible();
  });
});

describe('выгрузка спорных', () => {
  const REVIEW = {
    'GET /api/advertisers/niche?page=1': {
      body: { rows: [], waiting: 0, total: 0, page: 1, limit: 20 },
    },
    'GET /api/advertisers': {
      body: reviewQueue({ waiting: 3, counts: { pending: 5, bought: 1 } }),
    },
  };

  it('кнопка называет число, а файл сохраняется из ответа', async () => {
    const save = vi.fn(() => 'blob:файл');
    URL.createObjectURL = save;
    URL.revokeObjectURL = vi.fn();
    const recorded = open(
      {
        ...REVIEW,
        'GET /api/advertisers/pending/export': {
          raw: 'донор;домен\r\ndonor.example.test;one.example.test\r\n',
          headers: {
            'content-type': 'text/csv; charset=utf-8',
            'Content-Disposition': 'attachment; filename="disputed-2026-11-02.csv"',
            'X-Export-Rows': '5',
            'X-Export-Asked': '5',
          },
        },
      },
      '/advertisers?view=review',
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Выгрузить спорные · 5' }));

    expect(await screen.findByText('Выгружено спорных: 5.')).toBeVisible();
    expect(recorded.calls.map((call: Call) => call.path)).toContain(
      '/api/advertisers/pending/export',
    );
    const blob = save.mock.calls[0]?.[0 as never] as unknown as Blob;
    expect(await textOf(blob)).toContain('donor.example.test;one.example.test');
  });

  it('потолок файла сказан словами', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:файл');
    URL.revokeObjectURL = vi.fn();
    open(
      {
        ...REVIEW,
        'GET /api/advertisers/pending/export': {
          raw: 'донор',
          headers: {
            'content-type': 'text/csv; charset=utf-8',
            'X-Export-Rows': '10000',
            'X-Export-Asked': '12000',
          },
        },
      },
      '/advertisers?view=review',
    );
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Выгрузить спорные · 5' }));

    expect(await screen.findByText(/В файле 10 000 из 12 000/)).toBeVisible();
  });

  it('на вкладке «Куплено» кнопки нет', async () => {
    open(
      {
        ...REVIEW,
        'GET /api/advertisers?verdict=bought': { body: reviewQueue() },
      },
      '/advertisers?view=review',
    );
    const user = userEvent.setup();
    await screen.findByRole('button', { name: 'Выгрузить спорные · 5' });

    await user.click(screen.getByText(/Куплено · 1/));

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /Выгрузить спорные/ })).toBeNull(),
    );
  });
});
