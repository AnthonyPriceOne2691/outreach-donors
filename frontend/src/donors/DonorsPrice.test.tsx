/**
 * Цена в таблице доноров: строкой под доменом, фильтр цены, решение человека и порядок
 * над таблицей (сверка ТЗ 12.10.2026).
 *
 * ТЗ: «таблица проверенных доноров с ценами»; «Доноры — таблица со всеми сайтами,
 * статусом и объяснением, по какому порогу сайт отсеян. Фильтры, сортировка,
 * выгрузка»; «свежесть цены 150 дней; старше — сначала перезапрос». Проверяется, что
 * цена видна под доменом с датой или «устарела», что фильтр, решение и порядок уходят
 * на сервер параметрами и живут в адресе, а отмеченные выгружаются под решением экрана
 * и в его порядке. Своей колонки у цены нет: таблица на 1440 с раскрытым меню не уезжает
 * в прокрутку (`DonorsPage.test.tsx`, «домен встаёт в колонку»).
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { DonorRowCard } from '../api/types';
import { CONTACTS_STATE, page, ROW, ROWS } from '../test/donorFixtures';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';

const FIRST = 'limit=20&offset=0';

async function openDonors(
  routes: Record<string, Answer> = {},
  { path = '/donors', ready = 'good.example.test' }: { path?: string; ready?: string } = {},
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/contacts': { body: CONTACTS_STATE },
    [`GET /api/donors?${FIRST}`]: { body: page() },
    ...routes,
  });
  renderWith(<AppRoutes />, path);
  await screen.findByText(ready);
  return recorded;
}

function listCalls(recorded: Recorded): string[] {
  return recorded.calls
    .filter((call: Call) => call.method === 'GET' && call.path.startsWith('/api/donors?'))
    .map((call: Call) => call.path);
}

/** Текст без неразрывных пробелов разрядов и валюты. */
function plain(element: HTMLElement): string {
  return (element.textContent ?? '').replace(/[\u00a0\u202f]/g, ' ');
}

/** Ячейка донора: домен и под ним — причина, решение и цена. */
function donorCell(host: string): HTMLElement {
  return screen.getByText(host).closest('td')!;
}

async function choose(fieldName: string, option: string) {
  const user = userEvent.setup();
  const field = screen.getByRole('textbox', { name: fieldName });
  await user.click(field);
  const list = within(document.getElementById(field.getAttribute('aria-controls') ?? '')!);
  await user.click(await list.findByRole('option', { name: option, hidden: true }));
}

const PRICED: DonorRowCard = {
  ...ROW,
  last_price: '150.00',
  last_price_currency: 'EUR',
  last_price_at: '2026-10-05T10:00:00+00:00',
  price_stale: false,
};

describe('доноры: цена в строке', () => {
  it('под доменом — сумма и дата, устаревшая — «устарела», снятая ответом — его словами', async () => {
    const rows: DonorRowCard[] = [
      PRICED,
      { ...ROWS[1]!, last_price: '90.00', last_price_currency: 'USD', price_stale: true },
      { ...ROWS[2]!, seller_answer: 'declines' },
    ];
    await openDonors({ [`GET /api/donors?${FIRST}`]: { body: page({ rows }) } });

    expect(plain(donorCell('good.example.test'))).toContain('150,00 €05.10.2026');
    expect(within(donorCell('weak.example.test')).getByText('устарела')).toBeVisible();
    expect(within(donorCell('blank.example.test')).getByText('не продаёт')).toBeVisible();
    // Без цены и без ответа — под доменом ничего: «—» читалось бы «не спрашивали».
    expect(screen.queryAllByRole('columnheader', { name: 'Цена' })).toHaveLength(0);
  });

  it('фильтр цены над таблицей уходит параметром, со счётчиками в списке', async () => {
    const recorded = await openDonors({
      [`GET /api/donors?${FIRST}`]: {
        body: page({ prices: { priced: 2, fresh: 1, stale: 1, none: 1 } }),
      },
      [`GET /api/donors?price=stale&${FIRST}`]: {
        body: page({ rows: [{ ...PRICED, price_stale: true }], total: 1 }),
      },
    });

    await choose('Цена', 'устарела · 1');

    await waitFor(() => expect(listCalls(recorded)).toContain(`/api/donors?price=stale&${FIRST}`));
    expect(await screen.findByText('найдено 1 из 3')).toBeInTheDocument();
  });

  it('когда цена устарела — подсказкой у фильтра цены, срок — числом сервера', async () => {
    await openDonors({ [`GET /api/donors?${FIRST}`]: { body: page({ price_ttl_days: 150 }) } });
    const user = userEvent.setup();

    await user.hover(screen.getByRole('button', { name: 'Когда цена устарела' }));

    expect(await screen.findByText(/названа больше 150 дней назад/)).toBeInTheDocument();
  });

  it('пусто под фильтром цены — условие названо словами', async () => {
    await openDonors(
      { [`GET /api/donors?price=none&${FIRST}`]: { body: page({ rows: [], total: 0 }) } },
      { path: '/donors?price=none', ready: 'Под фильтр ничего не попало.' },
    );

    expect(screen.getByText(/Условия: без цены\./)).toBeInTheDocument();
  });
});

describe('доноры: решение человека и порядок', () => {
  // Скачивание файла: в jsdom ссылок на объекты нет — подставляем их, щелчок глушим.
  beforeEach(() => {
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(() => 'blob:donors'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      writable: true,
      value: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
  });

  afterEach(() => {
    Reflect.deleteProperty(URL, 'createObjectURL');
    Reflect.deleteProperty(URL, 'revokeObjectURL');
  });

  it('без выбора — доноры, как было: параметров решения и порядка в запросе нет', async () => {
    const recorded = await openDonors();

    expect(listCalls(recorded)).toEqual([`/api/donors?${FIRST}`]);
    expect(screen.getByRole('textbox', { name: 'Решение человека' })).toHaveValue('доноры');
    expect(screen.getByRole('textbox', { name: 'Порядок строк' })).toHaveValue('по DR');
  });

  it('«все проверенные» — параметром, не донор подписан под доменом', async () => {
    const rejected: DonorRowCard = { ...ROWS[1]!, review: 'rejected' };
    const recorded = await openDonors({
      [`GET /api/donors?${FIRST}`]: {
        body: page({ reviews: { accepted: 3, rejected: 1, undecided: 0, any: 4 } }),
      },
      [`GET /api/donors?review=any&${FIRST}`]: {
        body: page({ rows: [ROW, rejected], total: 2 }),
      },
    });

    await choose('Решение человека', 'все проверенные · 4');

    expect(await screen.findByText('не донор: отклонён')).toBeVisible();
    expect(listCalls(recorded)).toContain(`/api/donors?review=any&${FIRST}`);
    // Решение — выбор основы, а не сужение: «найдено N из M» он не пишет.
    expect(screen.queryByText(/^найдено/)).toBeNull();
  });

  it('другого решения нет вовсе — так и сказано, без пути к рассмотрению', async () => {
    await openDonors(
      {
        [`GET /api/donors?review=rejected&${FIRST}`]: {
          body: page({ rows: [], total: 0, counts: {} }),
        },
      },
      { path: '/donors?review=rejected', ready: 'Отклонённых нет.' },
    );

    expect(screen.queryByRole('link', { name: /Рассмотреть/ })).toBeNull();
  });

  it('порядок — параметром и в адресе, выгрузка — в том же порядке', async () => {
    const recorded = await openDonors({
      [`GET /api/donors?sort=price&${FIRST}`]: { body: page() },
      'GET /api/donors/export?sort=price': {
        raw: 'домен\r\n',
        headers: { 'Content-Type': 'text/csv', 'X-Export-Rows': '3', 'X-Export-Asked': '3' },
      },
    });

    await choose('Порядок строк', 'сначала дешёвые');
    await waitFor(() => expect(listCalls(recorded)).toContain(`/api/donors?sort=price&${FIRST}`));
    const user = userEvent.setup();
    // Порядок не сужает: выгружаются все.
    await user.click(await screen.findByRole('button', { name: 'Выгрузить всех · 3' }));

    await waitFor(() =>
      expect(recorded.calls.map((call) => call.path)).toContain('/api/donors/export?sort=price'),
    );
  });

  it('отмеченные выгружаются под решением экрана, где их отметили, и в его порядке', async () => {
    const recorded = await openDonors(
      {
        [`GET /api/donors?review=any&sort=price&${FIRST}`]: { body: page() },
        'POST /api/donors/export': {
          raw: 'домен\r\n',
          headers: { 'Content-Type': 'text/csv', 'X-Export-Rows': '1', 'X-Export-Asked': '1' },
        },
      },
      { path: '/donors?review=any&sort=price' },
    );
    const user = userEvent.setup();

    await user.click(screen.getByRole('checkbox', { name: 'Отметить weak.example.test' }));
    await user.click(screen.getByRole('button', { name: 'Выгрузить отмеченных · 1' }));

    await waitFor(() =>
      expect(recorded.calls.find((call) => call.method === 'POST')?.body).toEqual({
        ids: [2],
        review: 'any',
        sort: 'price',
      }),
    );
  });
});
