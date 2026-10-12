/**
 * Веса и пороги Этапа 2 на экране «Пороги» — только показ (сверка ТЗ 11.10.2026).
 *
 * Значения — настройки окружения, и экран говорит, какой: поля здесь нет, есть
 * значение и имя настройки. Карточка свёрнута и сервер не спрашивает, пока её
 * не раскрыли.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const THRESHOLDS = {
  current: null,
  defaults: {
    min_dr: 20,
    min_org_traffic: 500,
    min_refdomains: 100,
    min_keywords: 300,
    max_dr: 80,
    geo_top_n: 5,
    geo_min_share_percent: 20,
    metrics_ttl_days: 90,
  },
  history: [],
  limits: {
    min_dr: { min: 0, max: 90 },
    min_org_traffic: { min: 0, max: 10_000_000 },
    min_refdomains: { min: 0, max: 1_000_000 },
    min_keywords: { min: 0, max: 1_000_000 },
    max_dr: { min: 1, max: 100 },
  },
  rule_limits: {
    geo_top_n: { min: 1, max: 5 },
    geo_min_share_percent: { min: 1, max: 100 },
    metrics_ttl_days: { min: 1, max: 365 },
  },
};

const STAGE_TWO = {
  points: [{ label: 'rel=sponsored', value: 5, setting: 'ADVERTISERS_POINTS_REL_SPONSORED' }],
  thresholds: [
    { label: '«куплено» — баллов от', value: 4, setting: 'ADVERTISERS_BOUGHT_AT' },
    { label: 'кому не пишем — DR выше', value: 80, setting: 'ADVERTISERS_MAX_DR' },
  ],
  lists: [
    { label: 'крупные СМИ', values: ['news.example.test'], setting: 'ADVERTISERS_DENY_MEDIA' },
  ],
};

describe('Этап 2 на экране «Пороги»', () => {
  it('свёрнут и раскрывается значениями с именами настроек', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/settings/thresholds': { body: THRESHOLDS },
      'GET /api/settings/advertisers': { body: STAGE_TWO },
    });
    renderWith(<AppRoutes />, '/settings');
    const toggle = await screen.findByRole('button', {
      name: 'Рекламодатели: скоринг ссылок и «кому не пишем»',
    });

    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(recorded.calls.some((call) => call.path === '/api/settings/advertisers')).toBe(false);

    await userEvent.setup().click(toggle);

    expect(await screen.findByText('ADVERTISERS_MAX_DR')).toBeInTheDocument();
    expect(screen.getByText('кому не пишем — DR выше')).toBeInTheDocument();
    expect(screen.getByText('80')).toBeInTheDocument();
    expect(screen.getByText('news.example.test')).toBeInTheDocument();
    // Поле «Крупный сайт, DR выше» над карточкой — порог версии Этапа 1, а граница Этапа 2 —
    // только показ: поля с её подписью нет.
    expect(
      screen.queryByRole('textbox', { name: 'кому не пишем — DR выше' }),
    ).not.toBeInTheDocument();
  });

  it('отказ сервера — причина словами', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/settings/thresholds': { body: THRESHOLDS },
      'GET /api/settings/advertisers': { status: 503, body: { detail: 'Сервер недоступен' } },
    });
    renderWith(<AppRoutes />, '/settings');

    await userEvent.setup().click(
      await screen.findByRole('button', {
        name: 'Рекламодатели: скоринг ссылок и «кому не пишем»',
      }),
    );

    expect(await screen.findByText('Веса скоринга не загрузились')).toBeInTheDocument();
    expect(screen.getByText('Сервер недоступен')).toBeInTheDocument();
  });
});
