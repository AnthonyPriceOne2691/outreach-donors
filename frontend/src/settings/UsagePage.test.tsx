/**
 * Экран расхода: у каждого провайдера своя валюта счёта.
 *
 * Главное здесь — не показывать «0.00 $» там, где платят не деньгами.
 * До этого среза так и было: расход на выдачу не записывался вовсе,
 * и экран честно показывал по ней ноль долларов — то есть уверял,
 * что выдача досталась даром.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const SPENDING = {
  since: '2026-09-01T00:00:00+00:00',
  articles: [
    {
      provider: 'ahrefs',
      operation: 'batch_metrics',
      units: 6416,
      amount_usd: '0',
      calls: 12,
    },
    {
      provider: 'serp',
      operation: 'serp_search',
      units: 0,
      amount_usd: '0.3600',
      calls: 4,
    },
  ],
  units_by_provider: { ahrefs: 6416, llm: 1361 },
  amount_by_provider: { serp: '0.3600' },
  total_units: 7777,
  total_amount: '0.3600',
  ahrefs_left: 913000,
  ahrefs_cap: 100000,
  ahrefs_spent_by_us: 6416,
  ahrefs_left_error: null,
  serp_left_usd: '51.90',
  serp_spent_by_us: '0.3600',
  serp_left_error: null,
};

async function openUsage(spending: unknown = SPENDING) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/usage': { body: spending },
  });
  renderWith(<AppRoutes />, '/usage');
  await screen.findByText(/Мы потратили с начала месяца/);
}

describe('расход', () => {
  it('остаток у источника выдачи виден отдельно от Ahrefs', async () => {
    await openUsage();

    // Деньги и числа — по-русски: запятая в деньгах, разряды в больших числах.
    expect(screen.getByText(/на счету источника выдачи 51,90 \$/)).toBeInTheDocument();
    expect(screen.getByText(/у провайдера осталось 913\s000/)).toBeInTheDocument();
  });

  it('трата на выдачу показана в деньгах', async () => {
    await openUsage();

    expect(screen.getByText(/Выдача с начала месяца/)).toBeInTheDocument();
    expect(screen.getAllByText(/0,36 \$/).length).toBeGreaterThan(0);
  });

  it('статьи названы словами, а не кодами операций', async () => {
    await openUsage({
      ...SPENDING,
      articles: [
        ...SPENDING.articles,
        { provider: 'llm', operation: 'site_judge', calls: 3, units: 1200, amount_usd: '0' },
        { provider: 'llm', operation: 'brand_new_thing', calls: 1, units: 5, amount_usd: '0' },
      ],
    });

    expect(screen.getByText('судья площадок')).toBeInTheDocument();
    expect(screen.queryByText('site_judge')).not.toBeInTheDocument();
    // Незнакомая операция не пропадает и не выдаёт себя за знакомую.
    expect(screen.getByText('прочее (brand_new_thing)')).toBeInTheDocument();
  });

  it('там, где платят не деньгами, нулей в долларах нет', async () => {
    await openUsage({
      ...SPENDING,
      articles: [],
      units_by_provider: { ahrefs: 6416 },
      amount_by_provider: {},
      total_amount: '0',
      serp_spent_by_us: '0',
    });

    // У отправки трат не было — так и сказано словами и прочерком,
    // а не «0.00 $», которое читается как «платили деньгами, и вышло
    // бесплатно». Строка про выдачу — отдельный разговор: там ноль
    // действительно означает ноль долларов.
    expect(screen.getAllByText('трат не было').length).toBeGreaterThan(0);
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('недоступный остаток у выдачи не прячется', async () => {
    await openUsage({
      ...SPENDING,
      serp_left_usd: null,
      serp_left_error: 'Не удалось узнать остаток у источника выдачи.',
    });

    expect(screen.getByText('Не удалось узнать остаток у источника выдачи.')).toBeInTheDocument();
  });
});
