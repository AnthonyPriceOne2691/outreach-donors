/**
 * Обход доноров на экране «Рекламодатели» — начало Этапа 2 кнопками.
 *
 * Проверяется то, ради чего панель: видно, кого можно обойти и почему
 * остальных нельзя; запуск уходит на сервер списком отмеченных; ход —
 * числом страниц; отказ и «некому взять» сказаны словами, а не молчанием.
 * Цена — с валютой, ручная — с пометкой; донор, заведённый вручную, уходит
 * на сервер как вписан, и что вышло — сказано (07.10.2026).
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { CrawlRow } from '../api/crawls';
import { ADMIN, NO_NICHE, OPERATOR, PROMOTE_ROUTES, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import { crawlState } from './CrawlPanel';
import { enteredSummary } from './ManualDonor';

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

/** Текст без неразрывных пробелов разрядов и валюты: «150,00 $». */
function plain(text: string): string {
  return text.replace(/[\u00a0\u202f]/g, ' ');
}

const TARGETS = {
  donors: [
    {
      host: 'fresh.example',
      price: 150,
      currency: 'USD',
      source: 'reply',
      priced_at: '2026-10-01T10:00:00Z',
      crawl: null,
    },
    {
      host: 'running.example',
      price: 200,
      currency: 'EUR',
      source: 'manual',
      priced_at: '2026-09-30T10:00:00Z',
      crawl: crawl({}),
    },
  ],
  no_price: 3,
  stale_price: 2,
  supplier: 0,
  notes: [],
  max_pages: 1000,
  workers: 4,
};

const QUEUE = { rows: [], waiting: 0, counts: { bought: 1, pending: 0 } };

function open(routes: Record<string, unknown> = {}, who: unknown = ADMIN) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    ...PROMOTE_ROUTES,
    ...NO_NICHE,
    'GET /api/advertisers': { body: QUEUE },
    'GET /api/crawls/targets': { body: TARGETS },
    'GET /api/crawls': { body: { rows: [crawl({})], active: 1, workers: 4 } },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/advertisers');
  return recorded;
}

describe('обход доноров', () => {
  it('показывает, кого можно обойти, и почему остальных нельзя', async () => {
    open();

    const table = await screen.findByRole('table');
    expect(within(table).getByText('fresh.example')).toBeInTheDocument();
    expect(within(table).getByText(/идёт: 340 из 1\s000/)).toBeInTheDocument();
    expect(within(table).getByText('не обходили')).toBeInTheDocument();
    expect(screen.getByText('Не обходим: без цены — 3, цена протухла — 2.')).toBeInTheDocument();
  });

  it('идущий обход не отмечается второй раз', async () => {
    open();

    expect(await screen.findByRole('checkbox', { name: 'Обойти running.example' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Обойти fresh.example' })).toBeEnabled();
  });

  it('отмеченные уходят на сервер, и исход сказан словами', async () => {
    const recorded = open({
      'POST /api/crawls': {
        body: {
          queued: { 'fresh.example': 8 },
          busy: [],
          failed: [],
          refused: {},
          workers: 4,
        },
      },
    });
    const user = userEvent.setup();

    const button = await screen.findByRole('button', { name: 'Обойти отмеченных · 0' });
    expect(button).toBeDisabled();
    await user.click(screen.getByRole('checkbox', { name: 'Обойти fresh.example' }));
    await user.click(screen.getByRole('button', { name: 'Обойти отмеченных · 1' }));

    await waitFor(() =>
      expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
        hosts: ['fresh.example'],
      }),
    );
    expect(await screen.findByText('Поставлено: 1.')).toBeInTheDocument();
  });

  it('некому взять — сказано, а не тишина', async () => {
    open({ 'GET /api/crawls/targets': { body: { ...TARGETS, workers: 0 } } });

    expect(await screen.findByText('Обходчиков нет')).toBeInTheDocument();
  });

  it('обходить некого — что делать сначала', async () => {
    open({
      'GET /api/crawls/targets': {
        body: {
          ...TARGETS,
          donors: [],
          notes: ['Подходящих доноров в базе нет: сначала прогон Этапа 1.'],
        },
      },
    });

    expect(await screen.findByText(/сначала прогон Этапа 1/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Обойти отмеченных/ })).not.toBeInTheDocument();
  });

  it('без права запуска — список без отметок и кнопок', async () => {
    open({}, { ...(OPERATOR as object), permissions: ['view'] });

    expect(await screen.findByText('fresh.example')).toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: /Обойти/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Завести донора вручную' })).toBeNull();
  });

  it('цена — с валютой, как её назвали; ручная — с пометкой «вручную»', async () => {
    open();

    const table = await screen.findByRole('table');
    const fresh = within(table).getByText('fresh.example').closest('tr')!;
    const running = within(table).getByText('running.example').closest('tr')!;
    expect(plain(fresh.textContent ?? '')).toContain('150,00 $');
    expect(within(fresh).queryByText('вручную')).toBeNull();
    expect(plain(running.textContent ?? '')).toContain('200,00 €');
    expect(within(running).getByText('вручную')).toBeVisible();
  });

  it('остановленный разбором — красным и с причиной за «!»', async () => {
    const stopped = crawl({
      status: 'stopped',
      reason: 'остановлен разбором: воркер умер, продолжений 2 из 2 — смотрит человек',
    });
    open({
      'GET /api/crawls/targets': {
        body: { ...TARGETS, donors: [{ ...TARGETS.donors[1], crawl: stopped }] },
      },
      'GET /api/crawls': { body: { rows: [], active: 0, workers: 4 } },
    });
    const user = userEvent.setup();

    expect(await screen.findByText('остановлен — смотрит человек')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Почему остановлен' }));
    const told = await screen.findByRole('dialog', { name: 'Почему остановлен' });
    // Разбор зависших закрывает обходы, а не прогоны: подпись — про обходы.
    expect(told).toHaveTextContent('Закрыт разбором зависших обходов: воркер умер');
  });

  it('«куплено» — списком, с датой статьи', async () => {
    const recorded = open({
      'GET /api/advertisers?verdict=bought': {
        body: {
          rows: [
            {
              id: 3,
              donor_host: 'fresh.example',
              target_root: 'brand.example',
              points: 5,
              verdict: 'bought',
              reasons: ['пометка спонсорской ссылки (rel=sponsored) +5'],
              links: 1,
              pages: 1,
              best_page_url: 'https://fresh.example/post',
              best_anchor: 'Brand',
              best_published: '2014-03-12',
              confirmed: null,
              decided_by: null,
              decided_at: null,
            },
          ],
          waiting: 0,
          counts: { bought: 1 },
        },
      },
    });
    const user = userEvent.setup();

    await user.click(await screen.findByText('Куплено · 1'));

    expect(await screen.findByText('brand.example')).toBeInTheDocument();
    expect(screen.getByText('статья от 12.03.2014')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Рекламодатели: куплено' })).toBeInTheDocument();
    expect(recorded.calls.some((call) => call.path.endsWith('?verdict=bought'))).toBe(true);
  });
});

const ENTERED = {
  donor_id: 41,
  host: 'example.com',
  created: true,
  suitable: true,
  price: '150.00',
  currency: 'EUR',
};

describe('донор, заведённый вручную', () => {
  it('вписанное уходит на сервер, и таблица перечитывается', async () => {
    const recorded = open({ 'POST /api/donors': { body: ENTERED } });
    const user = userEvent.setup();

    const toggle = await screen.findByRole('button', { name: 'Завести донора вручную' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const enter = screen.getByRole('button', { name: 'Завести донора' });
    expect(enter).toBeDisabled();
    await user.type(
      screen.getByRole('textbox', { name: 'Домен' }),
      ' https://www.example.com/blog ',
    );
    // Домен без цены — нажимать рано: без цены донора не заводят.
    expect(enter).toBeDisabled();
    await user.type(screen.getByRole('textbox', { name: 'Цена' }), '150');
    await user.clear(screen.getByRole('textbox', { name: 'Валюта' }));
    await user.type(screen.getByRole('textbox', { name: 'Валюта' }), 'eur');
    await user.type(screen.getByRole('textbox', { name: 'Откуда цена' }), 'LinkDetective');
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
    // Заведён — поля свёрнуты, а список «кого обходить» перечитан: донор в нём.
    await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Домен' })).toBeNull());
    await waitFor(() =>
      expect(recorded.calls.filter((call) => call.path === '/api/crawls/targets')).toHaveLength(2),
    );
  });

  it('отказ сервера — его словами над полями, вписанное на месте', async () => {
    const refusal =
      'example.com в стоп-листе: ему не пишем, и донором с ценой его не заводим. Снять запись — на экране «Стоп-лист».';
    open({ 'POST /api/donors': { status: 409, body: { detail: refusal } } });
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Завести донора вручную' }));
    await user.type(screen.getByRole('textbox', { name: 'Домен' }), 'example.com');
    await user.type(screen.getByRole('textbox', { name: 'Цена' }), '150{Enter}');

    expect(await screen.findByText(refusal)).toBeVisible();
    expect(screen.getByText('Донора не завели')).toBeVisible();
    expect(screen.getByRole('textbox', { name: 'Домен' })).toHaveValue('example.com');
    expect(screen.getByRole('textbox', { name: 'Цена' })).toHaveValue('150');
  });

  it('обходить некого — завести донора вручную всё равно можно', async () => {
    open({
      'GET /api/crawls/targets': {
        body: { ...TARGETS, donors: [], notes: ['Подходящих доноров в базе нет.'] },
      },
    });

    expect(await screen.findByRole('button', { name: 'Завести донора вручную' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /Обойти отмеченных/ })).toBeNull();
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

describe('состояние обхода словами', () => {
  it.each([
    [{ status: 'queued' as const }, 'в очереди'],
    [{ status: 'done' as const, outcome: 'ok' as const, pages: 1 }, 'обойдён: 1 страница'],
    [{ status: 'done' as const, outcome: 'partial' as const, pages: 5 }, 'частично: 5 страниц'],
    [{ status: 'done' as const, outcome: 'forbidden' as const }, 'robots.txt запрещает'],
    [{ status: 'done' as const, outcome: 'blocked' as const }, 'закрылся от нас'],
    [{ status: 'done' as const, outcome: 'failed' as const }, 'не открылся'],
  ])('%o → %s', (overrides, label) => {
    expect(crawlState(crawl(overrides)).label).toBe(label);
  });
});
