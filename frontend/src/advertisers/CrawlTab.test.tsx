/**
 * Вкладка «Обход доноров» и сводка обхода в «Проверке» (29.10.2026).
 *
 * Проверяется то, ради чего вкладка: доноры — страницей с сервера, условия — в адресе и в
 * запросе, счётчики статусов — кнопками-фильтрами, отмечается только тот, кого можно обойти,
 * «Обойти всех по фильтру» — с числом и временем до нажатия, строка — в одну высоту, журнал —
 * свёрнут. Состояние обхода словами, доля закрытых, «Переобойти» и донор, заведённый вручную, —
 * то же, что было у панели обхода (`CrawlPanel`, до 29.10.2026).
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { CrawlDonorsView, CrawlRow, CrawlSummary, CrawlTarget } from '../api/crawls';
import {
  ADMIN,
  EMPTY_CRAWL_SUMMARY,
  NO_NICHE,
  OPERATOR,
  PROMOTE_ROUTES,
  TOKEN_KEY,
} from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { bulkCount, bulkTiming } from './BulkCrawl';
import { pickable } from './CrawlDonorsTable';
import { closedShare, crawlState, failureShare } from './CrawlTable';
import { crawlEta, readCrawlFilters, writeCrawlFilters } from './crawlFilters';
import { heldWords } from './crawlWords';
import { bulkSummary, launchSummary } from './crawlQueries';
import { enteredSummary } from './ManualDonor';
import { againRows } from './RecentCrawls';

function crawl(overrides: Partial<CrawlRow>): CrawlRow {
  return {
    id: 7,
    host: 'running.example',
    status: 'running',
    outcome: null,
    stop_reason: null,
    pages: 340,
    max_pages: 1000,
    articles: 300,
    links: null,
    resumes: 0,
    requested_by: 'claude@site.com',
    created_at: '2026-10-06T15:00:00Z',
    finished_at: null,
    reason: null,
    ...overrides,
  };
}

function donor(overrides: Partial<CrawlTarget>): CrawlTarget {
  return {
    host: 'fresh.example',
    price: 150,
    currency: 'USD',
    source: 'reply',
    priced_at: '2026-10-01T10:00:00Z',
    crawl: null,
    price_state: 'fresh',
    ...overrides,
  };
}

/** Текст без неразрывных пробелов разрядов и валюты: «150,00 $». */
function plain(text: string): string {
  return text.replace(/[\u00a0\u202f]/g, ' ');
}

const ROWS: CrawlTarget[] = [
  donor({}),
  donor({
    host: 'running.example',
    price: 200,
    currency: 'EUR',
    source: 'manual',
    crawl: crawl({}),
  }),
  donor({ host: 'stale.example', price_state: 'stale' }),
  donor({
    host: 'blocked.example',
    crawl: crawl({
      id: 5,
      host: 'blocked.example',
      status: 'done',
      outcome: 'blocked',
      stop_reason: 'no_start',
    }),
  }),
];

const COUNTS = {
  status: { never: 2, queued: 0, running: 1, done: 0, partial: 0, refused: 1, stopped: 0 },
  price: { fresh: 3, stale: 1, none: 0 },
};

const PAGE: CrawlDonorsView = {
  rows: ROWS,
  total: 4,
  page: 1,
  limit: 50,
  counts: COUNTS,
  startable: 2,
  bulk_max: 10_000,
  workers: 8,
  typical_crawl_sec: 1500,
  max_pages: 1000,
};

const SUMMARY: CrawlSummary = { counts: COUNTS, startable: 2, active: 1, workers: 8 };
/** Ничего не идёт — сводка не перечитывает доноров сама, и счёт запросов — только свой. */
const IDLE: CrawlSummary = {
  ...SUMMARY,
  counts: { ...COUNTS, status: { ...COUNTS.status, running: 0 } },
  active: 0,
};

const ENTERED = {
  donor_id: 41,
  host: 'example.com',
  created: true,
  suitable: true,
  price: '150.00',
  currency: 'EUR',
};

function open(
  routes: Record<string, unknown> = {},
  who: unknown = ADMIN,
  at = '/advertisers?view=crawl',
) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    ...PROMOTE_ROUTES,
    ...NO_NICHE,
    'GET /api/advertisers': { body: { rows: [], waiting: 0, counts: { bought: 1, pending: 0 } } },
    'GET /api/crawls/summary': { body: SUMMARY },
    'GET /api/crawls/donors': { body: PAGE },
    'GET /api/crawls': { body: { rows: [crawl({})], active: 1, workers: 8, total: 1 } },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, at);
  return recorded;
}

function paths(recorded: ReturnType<typeof open>, path: string): number {
  return recorded.calls.filter((call) => call.path === path).length;
}

/** Пункт списка одного поля: полей со списком на вкладке несколько. */
async function choose(fieldName: string, option: string) {
  const user = userEvent.setup();
  const field = screen.getByRole('textbox', { name: fieldName });
  await user.click(field);
  const list = within(document.getElementById(field.getAttribute('aria-controls') ?? '')!);
  await user.click(await list.findByRole('option', { name: option, hidden: true }));
}

describe('вкладка «Обход доноров»', () => {
  it('доноры — страницей с сервера; счётчики статусов — кнопками с числами', async () => {
    open();

    expect(await screen.findByRole('table', { name: 'Доноры для обхода' })).toBeInTheDocument();
    expect(await screen.findByText('fresh.example')).toBeInTheDocument();
    const facets = screen.getByRole('group', { name: 'Статус последнего обхода' });
    expect(within(facets).getByRole('button', { name: 'все · 4' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(within(facets).getByRole('button', { name: 'не пустил · 1' })).toBeInTheDocument();
    // Нулевой статус кнопкой не стоит: снимать нечего, нажимать — пусто.
    expect(within(facets).queryByRole('button', { name: /обойдён/ })).toBeNull();
  });

  it('счётчик — фильтр: уходит на сервер, нажат — подсвечен, остальные строки уходят', async () => {
    const recorded = open({
      'GET /api/crawls/donors?status=refused': { body: { ...PAGE, rows: [ROWS[3]!], total: 1 } },
    });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'не пустил · 1' }));

    await waitFor(() => expect(paths(recorded, '/api/crawls/donors?status=refused')).toBe(1));
    expect(await screen.findByRole('button', { name: 'не пустил · 1' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await waitFor(() => expect(screen.queryByText('fresh.example')).toBeNull());
  });

  it('фильтры — под своими колонками: срок цены и домен уходят на сервер', async () => {
    const recorded = open({
      'GET /api/crawls/summary': { body: IDLE },
      'GET /api/crawls/donors?price=stale': { body: { ...PAGE, rows: [ROWS[2]!], total: 1 } },
      'GET /api/crawls/donors?price=stale&search=stale': {
        body: { ...PAGE, rows: [ROWS[2]!], total: 1 },
      },
    });
    const user = userEvent.setup();

    await screen.findByText('fresh.example');
    await choose('Срок цены', 'протухла · 1');
    await waitFor(() => expect(paths(recorded, '/api/crawls/donors?price=stale')).toBe(1));
    await user.type(screen.getByRole('textbox', { name: 'Домен содержит' }), 'stale');

    // Поиск — после паузы в наборе: буквы по одной запросами не уходят.
    await waitFor(() =>
      expect(paths(recorded, '/api/crawls/donors?price=stale&search=stale')).toBe(1),
    );
    expect(recorded.calls.some((call) => call.path.includes('search=s&'))).toBe(false);
  });

  it('порядок и размер страницы — над таблицей, в запросе', async () => {
    const recorded = open({
      'GET /api/crawls/summary': { body: IDLE },
      'GET /api/crawls/donors?sort=crawled': { body: PAGE },
      'GET /api/crawls/donors?sort=crawled&size=100': { body: { ...PAGE, limit: 100 } },
    });

    await screen.findByText('fresh.example');
    await choose('Порядок строк', 'давно не обходили');
    await waitFor(() => expect(paths(recorded, '/api/crawls/donors?sort=crawled')).toBe(1));
    await choose('Доноров на странице', 'по 100');

    await waitFor(() =>
      expect(paths(recorded, '/api/crawls/donors?sort=crawled&size=100')).toBe(1),
    );
  });

  it('страницы — на сервере: номер уходит в запрос, отметки переживают страницу', async () => {
    const recorded = open({
      'GET /api/crawls/summary': { body: IDLE },
      'GET /api/crawls/donors': { body: { ...PAGE, total: 120 } },
      'GET /api/crawls/donors?page=2': {
        body: { ...PAGE, rows: [donor({ host: 'second.example' })], total: 120, page: 2 },
      },
    });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('checkbox', { name: 'Обойти fresh.example' }));
    const pages = screen.getByRole('navigation', { name: 'Страницы доноров' });
    await user.click(within(pages).getByRole('button', { name: 'Страница 2' }));

    expect(await screen.findByText('second.example')).toBeInTheDocument();
    expect(paths(recorded, '/api/crawls/donors?page=2')).toBe(1);
    // Отмеченный на первой странице — в счёте и на второй: отметка — набор, не страница.
    expect(screen.getByRole('button', { name: 'Обойти отмеченных · 1' })).toBeEnabled();
  });

  it('условия, порядок, размер и страница — из адреса: ссылка открывает тот же список', async () => {
    const recorded = open(
      {
        'GET /api/crawls/summary': { body: IDLE },
        'GET /api/crawls/donors?price=stale&sort=host&page=2&size=25': {
          body: { ...PAGE, rows: [ROWS[2]!], total: 30, page: 2, limit: 25 },
        },
      },
      ADMIN,
      '/advertisers?view=crawl&price=stale&sort=host&size=25&page=2',
    );

    expect(await screen.findByText('stale.example')).toBeInTheDocument();
    expect(paths(recorded, '/api/crawls/donors?price=stale&sort=host&page=2&size=25')).toBe(1);
    expect(paths(recorded, '/api/crawls/donors')).toBe(0);
  });

  it('отмечается только тот, кого можно обойти; отмеченные уходят на сервер, исход — словами', async () => {
    const recorded = open({
      'POST /api/crawls': {
        body: { queued: { 'fresh.example': 8 }, busy: [], failed: [], refused: {}, workers: 8 },
      },
    });
    const user = userEvent.setup();

    expect(
      await screen.findByRole('checkbox', { name: 'running.example: обход уже идёт или ждёт' }),
    ).toBeDisabled();
    expect(
      screen.getByRole('checkbox', { name: 'stale.example: цена протухла — сначала перезапрос' }),
    ).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Обойти отмеченных · 0' })).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: 'Обойти fresh.example' }));
    await user.click(screen.getByRole('button', { name: 'Обойти отмеченных · 1' }));

    await waitFor(() =>
      expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
        hosts: ['fresh.example'],
      }),
    );
    expect(await screen.findByText('Поставлено: 1.')).toBeInTheDocument();
    // Поставлены — отметки сняты: второе нажатие не поставит их ещё раз.
    expect(screen.getByRole('button', { name: 'Обойти отмеченных · 0' })).toBeDisabled();
  });

  it('«все на этой странице» — только тех, кого можно обойти', async () => {
    open();
    const user = userEvent.setup();

    await screen.findByText('fresh.example');
    await user.click(screen.getByRole('checkbox', { name: 'Отметить всех на этой странице' }));

    expect(screen.getByRole('button', { name: 'Обойти отмеченных · 2' })).toBeEnabled();
    expect(screen.getByRole('checkbox', { name: 'Обойти fresh.example' })).toBeChecked();
    expect(screen.getByRole('checkbox', { name: 'Обойти blocked.example' })).toBeChecked();
    expect(
      screen.getByRole('checkbox', { name: 'running.example: обход уже идёт или ждёт' }),
    ).not.toBeChecked();
  });

  it('«Обойти всех по фильтру» — окно с числом и временем; на сервер — условия и сколько видели', async () => {
    const recorded = open({
      'GET /api/crawls/donors?status=never': { body: PAGE },
      'POST /api/crawls/donors/crawl': {
        body: {
          asked: 2,
          deferred: false,
          queued: 2,
          busy: 0,
          failed: 0,
          job_id: null,
          workers: 8,
        },
      },
    });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'не обходили · 2' }));
    await waitFor(() => expect(paths(recorded, '/api/crawls/donors?status=never')).toBe(1));
    const bulk = screen.getByRole('button', { name: 'Обойти всех по фильтру · 2' });
    expect(bulk).toHaveAttribute('aria-haspopup', 'dialog');
    await user.click(bulk);
    const dialog = await screen.findByRole('dialog', { name: 'Обойти всех по фильтру?' });
    expect(dialog).toHaveTextContent('Встанут в очередь: 2 донора — не обходили.');
    expect(dialog).toHaveTextContent('При 8 обходчиках пройдут ≈ 25 мин');
    await user.click(within(dialog).getByRole('button', { name: 'Поставить 2' }));

    await waitFor(() =>
      expect(recorded.calls.find((call) => call.path === '/api/crawls/donors/crawl')?.body).toEqual(
        { status: 'never', expected: 2 },
      ),
    );
    expect(await screen.findByText('Поставлено: 2 из 2.')).toBeInTheDocument();
  });

  it('больше предела сервера — на кнопке «10 000 из …», и встанет не больше предела', async () => {
    const recorded = open({
      'GET /api/crawls/donors': { body: { ...PAGE, startable: 12_345 } },
      'POST /api/crawls/donors/crawl': {
        body: { asked: 10_000, deferred: true, job_id: 'bulk-1', workers: 8 },
      },
    });
    const user = userEvent.setup();

    await user.click(
      await screen.findByRole('button', {
        name: (name) => plain(name) === 'Обойти всех по фильтру · 10 000 из 12 345',
      }),
    );
    const dialog = await screen.findByRole('dialog', { name: 'Обойти всех по фильтру?' });
    await user.click(within(dialog).getByRole('button', { name: /^Поставить/ }));

    await waitFor(() =>
      expect(recorded.calls.find((call) => call.path === '/api/crawls/donors/crawl')?.body).toEqual(
        { expected: 10_000 },
      ),
    );
    expect(
      await screen.findByText(/^Ставим в очередь 10 000 доноров — фоном/, { normalizer: plain }),
    ).toBeInTheDocument();
  });

  it('строка — в одну высоту: причина обхода — в «i» у значка, а не под ним', async () => {
    open();

    const table = await screen.findByRole('table', { name: 'Доноры для обхода' });
    expect(await within(table).findByText('закрылся от нас')).toBeInTheDocument();
    expect(
      within(table).getByRole('button', { name: 'Подробнее: закрылся от нас' }),
    ).toBeInTheDocument();
    expect(within(table).queryByText(/защита от ботов/)).toBeNull();
  });

  it('цена — с валютой; ручная и протухшая — с пометкой', async () => {
    open();

    const table = await screen.findByRole('table', { name: 'Доноры для обхода' });
    const manual = (await within(table).findByText('running.example')).closest('tr')!;
    expect(plain(manual.textContent ?? '')).toContain('200,00 €');
    expect(manual).toHaveTextContent('вручную');
    expect(within(table).getByText('stale.example').closest('tr')!).toHaveTextContent('протухла');
  });

  it('«Переобойти» в строке — у законченного обхода, одним донором', async () => {
    const recorded = open({
      'POST /api/crawls': {
        body: { queued: { 'blocked.example': 9 }, busy: [], failed: [], refused: {}, workers: 8 },
      },
    });
    const user = userEvent.setup();

    const table = await screen.findByRole('table', { name: 'Доноры для обхода' });
    const again = await within(table).findByRole('button', { name: 'Переобойти blocked.example' });
    expect(within(table).queryByRole('button', { name: 'Переобойти running.example' })).toBeNull();
    await user.click(again);

    await waitFor(() =>
      expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
        hosts: ['blocked.example'],
      }),
    );
  });

  it('некому взять — сказано словами человека, а не тишиной и не командой консоли', async () => {
    open({ 'GET /api/crawls/donors': { body: { ...PAGE, workers: 0 } } });

    const alert = (await screen.findByText('Обходы некому взять')).closest('[role="alert"]')!;
    expect(alert).toHaveTextContent('служба обхода не запущена');
    expect(alert).not.toHaveTextContent(/docker|compose|crawler/);
  });

  it('машина занята — сколько обходчиков ждут и почему, словами сервера', async () => {
    const reason = 'нагрузка 0,93 на ядро при пороге 0,80';
    open({
      'GET /api/crawls/donors': { body: { ...PAGE, workers: 16, held: 2, held_reason: reason } },
    });

    const alert = (await screen.findByText('Машина занята')).closest('[role="alert"]')!;
    expect(alert).toHaveTextContent(`2 обходчика ждут, пока спадёт нагрузка: ${reason}.`);
    expect(alert).toHaveTextContent('поставленные начнутся сами, когда машина освободится');
  });

  it('сторож никого не держит или сервер старше — строки «Машина занята» нет', async () => {
    open({ 'GET /api/crawls/donors': { body: { ...PAGE, workers: 16, held: 0 } } });

    await screen.findByText('fresh.example');
    expect(screen.queryByText('Машина занята')).not.toBeInTheDocument();
  });

  it('пусто под фильтром — сказано, под каким, и сброс возвращает всех', async () => {
    const recorded = open(
      { 'GET /api/crawls/donors?status=stopped': { body: { ...PAGE, rows: [], total: 0 } } },
      ADMIN,
      '/advertisers?view=crawl&status=stopped',
    );
    const user = userEvent.setup();

    expect(await screen.findByText('Под фильтр никто не попал.')).toBeInTheDocument();
    expect(await screen.findByText('Условия: остановлен. Всего доноров — 4.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Сбросить фильтры' }));

    expect(await screen.findByText('fresh.example')).toBeInTheDocument();
    expect(paths(recorded, '/api/crawls/donors')).toBe(1);
  });

  it('доноров нет вовсе — что делать сначала, и завести вручную всё равно можно', async () => {
    open({
      'GET /api/crawls/summary': { body: EMPTY_CRAWL_SUMMARY },
      'GET /api/crawls/donors': {
        body: { ...PAGE, rows: [], total: 0, startable: 0, counts: EMPTY_CRAWL_SUMMARY.counts },
      },
    });

    expect(await screen.findByText('Обходить пока некого.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Завести донора вручную' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Обойти всех по фильтру · 0' })).toBeDisabled();
  });

  it('без права запуска — ни отметок, ни кнопок', async () => {
    open({}, { ...(OPERATOR as object), permissions: ['view'] });

    expect(await screen.findByText('fresh.example')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /Обойти|Отметить/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Обойти|Переобойти/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Завести донора вручную' })).toBeNull();
  });

  it('журнал обходов свёрнут и не спрашивается; раскрыть — страница с сервера', async () => {
    const recorded = open({ 'GET /api/crawls/summary': { body: IDLE } });
    const user = userEvent.setup();

    const toggle = await screen.findByRole('button', { name: /Журнал обходов/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(paths(recorded, '/api/crawls')).toBe(0);
    await user.click(toggle);

    expect(await screen.findByRole('table', { name: 'Последние обходы' })).toBeInTheDocument();
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(paths(recorded, '/api/crawls')).toBe(1);
  });
});

describe('донор, заведённый вручную', () => {
  it('вписанное уходит на сервер, окно закрывается, и вкладка перечитывается', async () => {
    const recorded = open({
      'GET /api/crawls/summary': { body: IDLE },
      'POST /api/donors': { body: ENTERED },
    });
    const user = userEvent.setup();

    const toggle = await screen.findByRole('button', { name: 'Завести донора вручную' });
    // Форма — в окне, а не раздвигает вкладку (аудит экранов 09.10.2026).
    expect(toggle).toHaveAttribute('aria-haspopup', 'dialog');
    await screen.findByText('fresh.example');
    expect(paths(recorded, '/api/crawls/donors')).toBe(1);
    await user.click(toggle);
    const dialog = await screen.findByRole('dialog', { name: 'Завести донора вручную' });
    const enter = within(dialog).getByRole('button', { name: 'Завести донора' });
    expect(enter).toBeDisabled();
    const field = (name: string) => within(dialog).getByRole('textbox', { name });
    await user.type(field('Домен'), ' https://www.example.com/blog ');
    // Домен без цены — нажимать рано: без цены донора не заводят.
    expect(enter).toBeDisabled();
    await user.type(field('Цена'), '150');
    await user.clear(field('Валюта'));
    await user.type(field('Валюта'), 'eur');
    await user.type(field('Откуда цена'), 'LinkDetective');
    await user.click(enter);

    expect(
      await screen.findByText(
        'Донор example.com заведён вручную: 150,00 €. Обход Этапа 2 его берёт.',
        { normalizer: plain },
      ),
    ).toBeInTheDocument();
    const sent = recorded.calls.filter((call) => call.method === 'POST');
    expect(sent.map((call) => [call.path, call.body])).toEqual([
      [
        '/api/donors',
        {
          host: 'https://www.example.com/blog',
          price: '150',
          currency: 'eur',
          note: 'LinkDetective',
        },
      ],
    ]);
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Завести донора вручную' })).toBeNull(),
    );
    await waitFor(() => expect(paths(recorded, '/api/crawls/donors')).toBe(2));
  });

  it('отказ сервера — его словами над полями, вписанное на месте', async () => {
    const refusal =
      'example.com в стоп-листе: ему не пишем, и донором с ценой его не заводим. Снять запись — на экране «Стоп-лист».';
    open({ 'POST /api/donors': { status: 409, body: { detail: refusal } } });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Завести донора вручную' }));
    const dialog = await screen.findByRole('dialog', { name: 'Завести донора вручную' });
    const field = (name: string) => within(dialog).getByRole('textbox', { name });
    await user.type(field('Домен'), 'example.com');
    await user.type(field('Цена'), '150{Enter}');

    expect(await within(dialog).findByText(refusal)).toBeVisible();
    expect(within(dialog).getByText('Донора не завели')).toBeVisible();
    expect(field('Домен')).toHaveValue('example.com');
    expect(field('Цена')).toHaveValue('150');
  });

  it.each([
    [ENTERED, 'Донор example.com заведён вручную: 150,00 €. Обход Этапа 2 его берёт.'],
    [
      { ...ENTERED, created: false },
      'example.com уже был донором — записана цена 150,00 €. Обход Этапа 2 его берёт.',
    ],
    [
      { ...ENTERED, created: false, suitable: false },
      'example.com уже был донором — записана цена 150,00 €. По порогам отбора он не годен — обход Этапа 2 его не возьмёт.',
    ],
  ])('что вышло — словами: %o', (entered, said) => {
    expect(plain(enteredSummary(entered))).toBe(said);
  });
});

describe('сводка обхода в «Проверке»', () => {
  it('одной строкой — что идёт и сколько можно обойти; кнопка ведёт на вкладку', async () => {
    const recorded = open({}, ADMIN, '/advertisers');
    const user = userEvent.setup();

    expect(await screen.findByText('можно обойти: 2')).toBeInTheDocument();
    expect(screen.getByText('идёт: 1')).toBeInTheDocument();
    expect(screen.getByText('не обходили 2 · идёт 1 · не пустил 1')).toBeInTheDocument();
    // Таблицы доноров в «Проверке» нет — и её запроса тоже.
    expect(paths(recorded, '/api/crawls/donors')).toBe(0);
    await user.click(screen.getByRole('button', { name: 'Открыть обход доноров' }));

    expect(await screen.findByRole('table', { name: 'Доноры для обхода' })).toBeInTheDocument();
    await waitFor(() => expect(paths(recorded, '/api/crawls/donors')).toBeGreaterThan(0));
  });

  it('ничего не идёт — без значков хода', async () => {
    open({ 'GET /api/crawls/summary': { body: EMPTY_CRAWL_SUMMARY } }, ADMIN, '/advertisers');

    expect(await screen.findByText('обходить сейчас некого')).toBeInTheDocument();
    expect(screen.queryByText(/идёт:/)).toBeNull();
    expect(screen.queryByText(/в очереди:/)).toBeNull();
  });
});

describe('состояние обхода словами', () => {
  it.each([
    [{ status: 'queued' as const }, 'в очереди'],
    // Страницы — против потолка: «из 1 000» видно, что до потолка не дошёл (23.10.2026).
    [
      { status: 'done' as const, outcome: 'ok' as const, pages: 1 },
      'обойдён: 1 из 1\u00a0000 страниц',
    ],
    [
      { status: 'done' as const, outcome: 'partial' as const, pages: 5 },
      'частично: 5 из 1\u00a0000 страниц',
    ],
    [{ status: 'done' as const, outcome: 'forbidden' as const }, 'robots.txt запрещает'],
    [{ status: 'done' as const, outcome: 'blocked' as const }, 'закрылся от нас'],
    [{ status: 'done' as const, outcome: 'failed' as const }, 'не открылся'],
  ])('%o → %s', (overrides, label) => {
    expect(crawlState(crawl(overrides)).label).toBe(label);
  });
});

describe('доля закрытых страниц у строки обхода', () => {
  it.each([
    [{ blocked_share: 0.125, requests: 480 }, 'закрыто 12,5% из 480 запросов'],
    [{ blocked_share: 0.083, requests: 48 }, 'закрыто 8,3% из 48 запросов'],
    [{ blocked_share: 1, requests: 21 }, 'закрыто 100% из 21 запроса'],
    [{ blocked_share: 0, requests: 1000 }, 'закрыто 0% из 1\u00a0000 запросов'],
  ])('%o → %s', (overrides, said) => {
    expect(closedShare(crawl(overrides))).toBe(said);
  });

  it('идущий и записанный до замера — без доли, а не «закрыто 0%»', () => {
    expect(closedShare(crawl({}))).toBeNull();
    expect(closedShare(crawl({ blocked_share: null, requests: null }))).toBeNull();
    expect(failureShare(crawl({}))).toBeNull();
  });

  it.each([
    [{ failure_share: 0.042, window: 480 }, '403/429/5xx/таймаут — 4,2% за последние 480 запросов'],
    [{ failure_share: 0.31, window: 500 }, '403/429/5xx/таймаут — 31% за последние 500 запросов'],
    [{ failure_share: 0.1, window: 21 }, '403/429/5xx/таймаут — 10% за последние 21 запроса'],
  ])('мера здоровья ТЗ словами ТЗ: %o → %s', (overrides, said) => {
    expect(failureShare(crawl(overrides))).toBe(said);
  });
});

describe('«Переобойти» в журнале обходов', () => {
  it('кнопка — у последнего обхода донора, чем бы он ни кончился, и донору со свежей ценой', () => {
    const rows = [
      crawl({ id: 9, host: 'again.example', status: 'done', outcome: 'partial' }),
      crawl({ id: 8, host: 'again.example', status: 'done', outcome: 'failed' }),
      crawl({ id: 7, host: 'whole.example', status: 'done', outcome: 'ok' }),
      crawl({ id: 6, host: 'robots.example', status: 'done', outcome: 'forbidden' }),
      crawl({ id: 5, host: 'closed.example', status: 'done', outcome: 'blocked' }),
      crawl({ id: 4, host: 'broken.example', status: 'done', outcome: 'failed' }),
      crawl({ id: 3, host: 'hung.example', status: 'stopped' }),
      crawl({ id: 2, host: 'stale.example', status: 'done', outcome: 'ok' }),
    ];
    const eligible = new Set(
      rows.map((row) => row.host).filter((host) => host !== 'stale.example'),
    );

    expect([...againRows(rows, eligible)]).toEqual([9, 7, 6, 5, 4, 3]);
    // Журнал вкладки не знает цен страницы: решает сервер, кнопка — у каждого законченного.
    expect([...againRows(rows, null)]).toEqual([9, 7, 6, 5, 4, 3, 2]);
  });

  it('пока переобход в очереди или идёт — кнопки нет, и у прежнего обхода донора тоже', () => {
    const rows = [
      crawl({ id: 4, host: 'busy.example', status: 'running' }),
      crawl({ id: 3, host: 'waiting.example', status: 'queued' }),
      crawl({ id: 2, host: 'busy.example', status: 'done', outcome: 'ok' }),
      crawl({ id: 1, host: 'waiting.example', status: 'done', outcome: 'partial' }),
    ];

    expect([...againRows(rows, new Set(['busy.example', 'waiting.example']))]).toEqual([]);
    expect([...againRows(rows, null)]).toEqual([]);
  });
});

describe('правила вкладки', () => {
  it('отметить можно только со свежей ценой и без идущего или ждущего обхода', () => {
    expect(pickable(donor({}))).toBe(true);
    expect(pickable(donor({ price_state: 'stale' }))).toBe(false);
    expect(pickable(donor({ price: null, price_state: 'none' }))).toBe(false);
    expect(pickable(donor({ crawl: crawl({ status: 'queued' }) }))).toBe(false);
    expect(pickable(donor({ crawl: crawl({ status: 'done', outcome: 'ok' }) }))).toBe(true);
  });

  it('условия — в адрес и обратно; незнакомое в адресе не сужает', () => {
    const filters = readCrawlFilters(
      new URLSearchParams('status=refused&price=fresh&sort=links&size=100&page=3'),
    );
    expect(filters).toMatchObject({
      status: 'refused',
      price: 'fresh',
      sort: 'links',
      size: 100,
      page: 3,
    });
    expect(writeCrawlFilters(filters).toString()).toBe(
      'view=crawl&status=refused&price=fresh&sort=links&size=100&page=3',
    );
    expect(readCrawlFilters(new URLSearchParams('status=all&size=7&sort=x&page=-2'))).toMatchObject(
      { status: null, size: 50, sort: 'priced', page: 1 },
    );
  });

  it('ждущие обходчики словами: число и слово согласованы, причины нет — без двоеточия', () => {
    expect(heldWords(1, null)).toMatch(/^1 обходчик ждёт, пока спадёт нагрузка\. /);
    expect(heldWords(3, 'свободно 6,2 ГБ памяти при пороге 8')).toMatch(
      /^3 обходчика ждут, пока спадёт нагрузка: свободно 6,2 ГБ памяти при пороге 8\. /,
    );
    expect(heldWords(16, null)).toMatch(/^16 обходчиков ждут/);
  });

  it('сколько займёт — по числу обходчиков и обычной длине обхода', () => {
    expect(crawlEta(16, 1500, 8)).toBe('≈ 50 мин');
    expect(crawlEta(100, 1500, 8)).toBe('≈ 5 ч');
    expect(crawlEta(10_000, 1500, 16)).toBe('≈ 11 сут');
    expect(crawlEta(5, 1500, 0)).toBeNull();
    expect(bulkTiming(5, 1500, null)).toMatch(/^Обходчиков сейчас не видно/);
    expect(bulkTiming(5, 1500, 1)).toMatch(/^При 1 обходчике пройдут/);
  });

  it('итог нажатия — словами; предел сервера — числом в подписи', () => {
    const done = {
      asked: 3,
      deferred: false,
      queued: 2,
      busy: 1,
      failed: 0,
      job_id: null,
      workers: 8,
    };
    expect(bulkSummary(done)).toBe('Поставлено: 2 из 3. Уже шли: 1.');
    expect(bulkSummary({ ...done, asked: 120, deferred: true, queued: null })).toBe(
      'Ставим в очередь 120 доноров — фоном, ход виден по счётчику «в очереди».',
    );
    expect(bulkSummary({ ...done, asked: 0 })).toMatch(/^Под фильтром обойти некого/);
    expect(plain(bulkCount(12_345, 10_000))).toBe('10 000 из 12 345');
    expect(bulkCount(12, 10_000)).toBe('12');
    expect(bulkCount(12, undefined)).toBe('12');
    expect(
      launchSummary({ queued: { a: 1 }, busy: [], failed: ['b'], refused: {}, workers: 8 }),
    ).toBe('Поставлено: 1. Очередь не ответила: b — попробуйте ещё раз.');
  });
});
