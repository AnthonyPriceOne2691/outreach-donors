/**
 * Таблица доноров: вердикт, причина отсева и фильтры.
 *
 * Отдельно проверяется, что «не проверен» и «не подходит» — разные
 * состояния: спутать их значит копить ложные отказы.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const ROWS = [
  {
    id: 1,
    host: 'good.example.test',
    status: 'suitable',
    reject_reason: null,
    dr: 45,
    org_traffic: 120000,
    geo: 'us',
    geo_top_share: 0.62,
    contacts: 2,
    contact_status: 'found',
    last_price: null,
    last_price_currency: null,
    metrics_refreshed_at: '2026-09-18T10:00:00+00:00',
    fresh: true,
  },
  {
    id: 2,
    host: 'weak.example.test',
    status: 'unsuitable',
    reject_reason: 'dr ниже порога',
    dr: 8,
    org_traffic: 300,
    geo: 'de',
    geo_top_share: 0.4,
    contacts: 0,
    contact_status: null,
    last_price: null,
    last_price_currency: null,
    metrics_refreshed_at: '2026-06-01T10:00:00+00:00',
    fresh: false,
  },
];

const PAGE = {
  rows: ROWS,
  total: 2,
  counts: { suitable: 1, unsuitable: 1, unchecked: 3 },
};

const CONTACTS = { pending: 3, running: false, job_id: null, last: null, workers: 1 };

async function openDonors(routes: Record<string, unknown> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/contacts': { body: CONTACTS },
    'GET /api/donors?limit=50&offset=0': { body: PAGE },
    'GET /api/donors?status=unchecked&limit=50&offset=0': {
      body: { rows: [], total: 0, counts: PAGE.counts },
    },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/donors');
  await screen.findByText('good.example.test');
  return recorded;
}

describe('доноры', () => {
  it('поиск контактов запускается с экрана, а не из консоли', async () => {
    const recorded = await openDonors({
      'POST /api/contacts': { body: { job_id: 'job-1', pending: 3 } },
    });
    const user = userEvent.setup();

    // До этой кнопки между «прогон нашёл доноров» и «собрать письма»
    // стоял инженер с консолью.
    await user.click(await screen.findByRole('button', { name: 'Найти контакты' }));

    await screen.findByText(/Поиск поставлен в очередь/);
    const sent = recorded.calls.filter((call) => call.method === 'POST');
    expect(sent[0]?.path).toBe('/api/contacts');
  });

  it('выгрузка уносит тот же фильтр, что на экране', async () => {
    await openDonors();

    const link = screen.getByRole('link', { name: 'Выгрузить' });
    expect(link).toHaveAttribute('href', '/api/donors/export?');
  });

  it('показывает вердикт и причину отсева', async () => {
    await openDonors();

    const row = screen.getByText('weak.example.test').closest('tr');
    expect(within(row as HTMLElement).getByText('не подходит')).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText('dr ниже порога')).toBeInTheDocument();
  });

  it('«не проверен» стоит отдельным счётчиком, а не вместе с «не подходит»', async () => {
    await openDonors();

    expect(screen.getByText('не проверен — 3')).toBeInTheDocument();
    expect(screen.getByText('не подходит — 1')).toBeInTheDocument();
  });

  it('свежесть данных видна в таблице: за свежие второй раз не платят', async () => {
    await openDonors();

    const good = screen.getByText('good.example.test').closest('tr');
    const weak = screen.getByText('weak.example.test').closest('tr');
    expect(within(good as HTMLElement).getByText('в сроке')).toBeInTheDocument();
    expect(within(weak as HTMLElement).getByText('пора обновить')).toBeInTheDocument();
  });

  it('счётчик работает фильтром и уходит на сервер', async () => {
    const recorded = await openDonors();
    const user = userEvent.setup();

    await user.click(screen.getByText('не проверен — 3'));

    expect(await screen.findByText(/всего доноров/)).toBeInTheDocument();
    expect(recorded.calls.some((call) => call.path.includes('status=unchecked'))).toBe(true);
  });
});
