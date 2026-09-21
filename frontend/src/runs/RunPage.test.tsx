/**
 * Экран прогона: кнопка, которая не нажимается без сметы.
 *
 * Это единственный экран, где человек тратит деньги, и проверяется здесь
 * ровно то, ради чего он такой: нельзя запустить, не увидев цену, и нельзя
 * запустить, если цена не помещается в остаток.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const FITS = {
  keywords: 2,
  depth_pages: 1,
  expected_results: 20,
  expected_domains: 3,
  units_screen: 50,
  units_metrics: 72,
  units_by_country: 55,
  units_total: 177,
  units_left: 100000,
  units_cap: 100000,
  budget: 100000,
  affordable: true,
  shortfall: 0,
};

const TOO_MUCH = { ...FITS, units_left: 100, budget: 100, affordable: false, shortfall: 77 };

const QUEUED = {
  id: 7,
  status: 'queued',
  country: 'us',
  keywords: 2,
  estimated_units: null,
  actual_units: null,
  estimate_error: null,
  stats: null,
  started_at: '2026-09-21T10:00:00Z',
  alive_at: '2026-09-21T10:00:00Z',
  hosts: null,
};

const STOPPED = {
  ...QUEUED,
  id: 6,
  status: 'stopped',
  estimated_units: 300,
  actual_units: 120,
  estimate_error: -0.6,
  hosts: 42,
  stats: { причина: 'остановлен разбором: воркер умер, продолжений 2 из 2' },
};

async function openRun(routes: Record<string, unknown> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/runs/countries': { body: ['us', 'de'] },
    'GET /api/runs': { body: { runs: [], workers: 1 } },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/run');
  await screen.findByLabelText('Ключевые слова');
  return recorded;
}

describe('прогон', () => {
  it('без сметы запускать нечего', async () => {
    await openRun();

    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('смета показывает, во что обойдётся, и открывает запуск', async () => {
    const recorded = await openRun({ 'POST /api/runs/estimate': { body: FITS } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    expect(await screen.findByText('до 177')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeEnabled();
    // Смета — отдельный запрос, который ничего не тратит.
    expect(recorded.calls.some((call) => call.path === '/api/runs/estimate')).toBe(true);
  });

  it('не помещается — кнопка не нажимается и сказано, чего не хватает', async () => {
    await openRun({ 'POST /api/runs/estimate': { body: TOO_MUCH } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));

    expect(await screen.findByText('Не помещается в бюджет')).toBeInTheDocument();
    expect(screen.getByText(/Не хватает 77 юнитов/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('смета устаревает вместе со списком ключей', async () => {
    await openRun({ 'POST /api/runs/estimate': { body: FITS } });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));
    await screen.findByText('до 177');

    await user.type(screen.getByLabelText('Ключевые слова'), '\nтретий ключ');

    expect(await screen.findByText('Смета устарела')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Запустить/ })).toBeDisabled();
  });

  it('прогон в очереди виден до первой траты', async () => {
    await openRun({ 'GET /api/runs': { body: { runs: [QUEUED], workers: 1 } } });

    expect(await screen.findByText('в очереди')).toBeInTheDocument();
    // Смета и домены появятся позже — но сам прогон на экране уже есть.
    expect(screen.getByText('№7')).toBeInTheDocument();
  });

  it('очередь без воркера — это не работающий сервис', async () => {
    await openRun({ 'GET /api/runs': { body: { runs: [QUEUED], workers: 0 } } });

    expect(await screen.findByText('Задачу некому взять')).toBeInTheDocument();
  });

  it('пока воркер жив, про него ничего не говорят', async () => {
    await openRun({ 'GET /api/runs': { body: { runs: [QUEUED], workers: 1 } } });
    await screen.findByText('в очереди');

    expect(screen.queryByText('Задачу некому взять')).not.toBeInTheDocument();
  });

  it('у остановленного прогона видна причина, а не пустая ячейка', async () => {
    await openRun({ 'GET /api/runs': { body: { runs: [STOPPED], workers: 1 } } });

    expect(await screen.findByText(/воркер умер/)).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('запуск кладёт задачу в очередь, а не ждёт прогона', async () => {
    const recorded = await openRun({
      'POST /api/runs/estimate': { body: FITS },
      'POST /api/runs': {
        body: { run_id: 7, job_id: 'abc-123', note: 'Прогон встал в очередь.' },
      },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Ключевые слова'), 'ремонт\nдизайн');
    await user.click(screen.getByRole('button', { name: 'Посчитать смету' }));
    await screen.findByText('до 177');
    await user.click(screen.getByRole('button', { name: /Запустить/ }));

    await waitFor(() => {
      expect(screen.getByText('Прогон в очереди')).toBeInTheDocument();
    });
    // По пути мало: историю прогонов читает GET по тому же адресу.
    const launch = recorded.calls.find(
      (call) => call.path === '/api/runs' && call.method === 'POST',
    );
    expect(launch?.body).toEqual({
      keywords: ['ремонт', 'дизайн'],
      country: 'us',
      depth_pages: 1,
    });
  });
});
