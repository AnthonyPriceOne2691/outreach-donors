/**
 * Протухшие цены на панели «Обход доноров» — «Перезапросить цену» по одному и пачкой
 * (12.10.2026).
 *
 * Проверяется то, ради чего строка: сколько протухших — видно на панели, список с тем,
 * куда уйдёт письмо и почему нельзя, — в окне и словами сервера; на сервер уходят номера
 * отмеченных; итог — в том же окне; без права писать наружу — список без отметок и кнопок.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { CrawlTargets } from '../api/crawls';
import type { RecheckResult, RecheckRow, RechecksView } from '../api/priceRecheck';
import { recheckSummary } from '../api/priceRecheck';
import { ADMIN, CRAWL_ROUTES, NO_NICHE, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer } from '../test/server';

function row(overrides: Partial<RecheckRow>): RecheckRow {
  return {
    donor_id: 7,
    host: 'stale.example.test',
    price: 90,
    currency: 'USD',
    source: 'reply',
    priced_at: '2026-03-20T10:00:00Z',
    asked_at: null,
    refusal: null,
    way: 'thread',
    thread_id: 12,
    email: 'boss@stale.example.test',
    mailbox: 'anna@mail.example.test',
    ...overrides,
  };
}

const VIEW: RechecksView = {
  rows: [
    row({}),
    row({
      donor_id: 8,
      host: 'manual.example.test',
      source: 'manual',
      way: 'first',
      thread_id: null,
      email: 'desk@manual.example.test',
      mailbox: null,
    }),
    row({
      donor_id: 9,
      host: 'silent.example.test',
      way: null,
      thread_id: null,
      email: null,
      mailbox: null,
      refusal:
        'silent.example.test не ответил на наши письма (переписка №4): ещё одно письмо тому, кто промолчал, — путь к жалобе на спам.',
    }),
  ],
  total: 3,
  ttl_days: 150,
  every_days: 14,
  batch_max: 20,
};

/** Обходить некого, у троих цена протухла: строка панели и окно перезапроса. */
const TARGETS: CrawlTargets = {
  donors: [],
  no_price: 0,
  stale_price: 3,
  supplier: 0,
  notes: [],
  max_pages: 1000,
  workers: 4,
};

function open(routes: Record<string, Answer> = {}, who: unknown = ADMIN) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    ...CRAWL_ROUTES,
    ...NO_NICHE,
    'GET /api/advertisers': { body: { rows: [], waiting: 0, counts: { bought: 0, pending: 0 } } },
    'GET /api/crawls/targets': { body: TARGETS },
    'GET /api/price-rechecks': { body: VIEW },
    ...routes,
  });
  renderWith(<AppRoutes />, '/advertisers');
  return recorded;
}

async function list(): Promise<HTMLElement> {
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: 'Перезапросить цену' }));
  const dialog = await screen.findByRole('dialog', { name: 'Перезапрос цены' });
  await within(dialog).findByText('stale.example.test');
  // Окно открывается переходом (`opacity` от нуля): «виден ли» — когда переход кончился.
  await waitFor(() => expect(dialog).toBeVisible());
  return dialog;
}

describe('протухшие цены на панели обхода', () => {
  it('строка говорит, сколько их, а список в окне — куда уйдёт письмо или почему нет', async () => {
    open();

    expect(
      await screen.findByText(/Цена протухла у 3 доноров — их не обходим/),
    ).toBeInTheDocument();
    const dialog = await list();

    const thread = within(dialog).getByText('stale.example.test').closest('tr')!;
    expect(within(thread).getByRole('link', { name: 'переписку №12' })).toHaveAttribute(
      'href',
      '/threads/12',
    );
    expect(thread).toHaveTextContent('на boss@stale.example.test');
    const first = within(dialog).getByText('manual.example.test').closest('tr')!;
    expect(first).toHaveTextContent('новым письмом на desk@manual.example.test');
    expect(within(first).getByText('вручную')).toBeVisible();
    const silent = within(dialog).getByText('silent.example.test').closest('tr')!;
    expect(silent).toHaveTextContent('не ответил на наши письма');
    expect(
      within(silent).getByRole('checkbox', { name: 'Перезапросить цену silent.example.test' }),
    ).toBeDisabled();
    expect(within(dialog).getByText(/не чаще раза в 14 дней/)).toBeInTheDocument();
  });

  it('по одному — номер донора уходит на сервер, итог — в окне', async () => {
    const done: RecheckResult = {
      sent: [
        {
          donor_id: 7,
          host: 'stale.example.test',
          message_id: 40,
          thread_id: 12,
          way: 'thread',
          sender_email: 'anna@mail.example.test',
          real: true,
        },
      ],
      refused: [],
    };
    const recorded = open({ 'POST /api/price-rechecks': { body: done } });
    const user = userEvent.setup();
    const dialog = await list();

    const thread = within(dialog).getByText('stale.example.test').closest('tr')!;
    await user.click(
      within(thread).getByRole('button', { name: 'Перезапросить цену stale.example.test' }),
    );

    await waitFor(() =>
      expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
        donor_ids: [7],
      }),
    );
    expect(
      await within(dialog).findByText('Перезапрос цены ушёл: stale.example.test.'),
    ).toBeInTheDocument();
  });

  it('пачкой — отмеченные; отказ одного — словами сервера рядом с ушедшими', async () => {
    const done: RecheckResult = {
      sent: [
        {
          donor_id: 8,
          host: 'manual.example.test',
          message_id: 41,
          thread_id: 30,
          way: 'first',
          sender_email: 'anna@mail.example.test',
          real: true,
        },
      ],
      refused: [
        {
          donor_id: 7,
          host: 'stale.example.test',
          reason: 'Ящик anna@mail.example.test сейчас не пишет (выключен), а переписку ведёт он.',
        },
      ],
    };
    const recorded = open({ 'POST /api/price-rechecks': { body: done } });
    const user = userEvent.setup();
    const dialog = await list();

    const button = within(dialog).getByRole('button', { name: 'Перезапросить отмеченных · 0' });
    expect(button).toBeDisabled();
    await user.click(
      within(dialog).getByRole('checkbox', { name: 'Перезапросить цену stale.example.test' }),
    );
    await user.click(
      within(dialog).getByRole('checkbox', { name: 'Перезапросить цену manual.example.test' }),
    );
    await user.click(within(dialog).getByRole('button', { name: 'Перезапросить отмеченных · 2' }));

    await waitFor(() =>
      expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
        donor_ids: [7, 8],
      }),
    );
    expect(
      await within(dialog).findByText(
        'Перезапрос цены ушёл: manual.example.test. stale.example.test: Ящик anna@mail.example.test сейчас не пишет (выключен), а переписку ведёт он.',
      ),
    ).toBeInTheDocument();
  });

  it('без права писать наружу — список без отметок и кнопок', async () => {
    open({}, OPERATOR);
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Кому перезапросить цену' }));
    const dialog = await screen.findByRole('dialog', { name: 'Перезапрос цены' });

    expect(await within(dialog).findByText('stale.example.test')).toBeInTheDocument();
    expect(within(dialog).queryByRole('checkbox')).toBeNull();
    expect(within(dialog).queryByRole('button', { name: /Перезапросить/ })).toBeNull();
  });

  it('протухших нет — строки нет, и сервер о них не спрашивают', async () => {
    const recorded = open({ 'GET /api/crawls/targets': { body: { ...TARGETS, stale_price: 0 } } });

    expect(await screen.findByText('Обход доноров')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Перезапросить цену' })).toBeNull();
    expect(recorded.calls.some((call) => call.path.startsWith('/api/price-rechecks'))).toBe(false);
  });
});

/** Запросы POST ждут, пока тест не отпустит: пачка «в пути», пока её не отпустили. */
function holdPosts(): () => void {
  const served = vi.mocked(globalThis.fetch).getMockImplementation();
  if (served === undefined) throw new Error('сервер теста не записан');
  let release = () => {};
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
    if ((init?.method ?? 'GET').toUpperCase() === 'POST') await gate;
    return served(input, init);
  });
  return release;
}

describe('пачка в пути', () => {
  it('окно закрыли посреди пачки и открыли снова — кнопки заперты до её итога', async () => {
    open({ 'POST /api/price-rechecks': { body: { sent: [], refused: [] } } });
    const release = holdPosts();
    const user = userEvent.setup();
    const dialog = await list();

    await user.click(
      within(dialog).getByRole('button', { name: 'Перезапросить цену stale.example.test' }),
    );
    await user.keyboard('{Escape}');
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Перезапрос цены' })).not.toBeInTheDocument(),
    );
    const again = await list();

    expect(
      within(again).getByRole('button', { name: 'Перезапросить цену manual.example.test' }),
    ).toBeDisabled();
    release();
    await waitFor(() =>
      expect(
        within(again).getByRole('button', { name: 'Перезапросить цену manual.example.test' }),
      ).toBeEnabled(),
    );
  });
});

describe('итог перезапроса словами', () => {
  it('нулевая почта — так и сказано; номер без домена — с точкой', () => {
    const said = recheckSummary({
      sent: [
        {
          donor_id: 7,
          host: 'stale.example.test',
          message_id: 40,
          thread_id: 12,
          way: 'thread',
          sender_email: 'anna@mail.example.test',
          real: false,
        },
      ],
      refused: [{ donor_id: 99, host: null, reason: 'Донора №99 нет' }],
    });

    expect(said).toBe(
      'Перезапрос помечен отправленным, но наружу НЕ ушёл — транспорт не настроен: stale.example.test. Донора №99 нет.',
    );
  });
});
