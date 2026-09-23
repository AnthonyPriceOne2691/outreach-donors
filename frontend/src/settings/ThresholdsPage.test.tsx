/**
 * Пороги: предпросмотр до сохранения и новая версия вместо правки.
 *
 * Проверяется то, ради чего экран такой: человек видит последствия
 * до нажатия, а сохранение не переписывает прошлые вердикты.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const CURRENT = {
  version: 2,
  created_by: 'ivan@site.com',
  created_at: '2026-09-18T10:00:00+00:00',
  min_dr: 20,
  min_org_traffic: 500,
  min_refdomains: 100,
  min_keywords: 300,
};

const VIEW = {
  current: CURRENT,
  defaults: { min_dr: 20, min_org_traffic: 500, min_refdomains: 100, min_keywords: 300 },
  history: [CURRENT],
};

const CONSEQUENCES = {
  checked: 111,
  suitable_now: 105,
  suitable_after: 92,
  falls_out: 13,
  falls_out_with_price: 4,
  comes_back: 0,
  unchecked: 6,
};

async function openThresholds(routes: Record<string, unknown> = {}) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/settings/thresholds': { body: VIEW },
    'POST /api/settings/preview': { body: CONSEQUENCES },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/settings');
  await screen.findByRole('heading', { name: 'Пороги отбора' });
  return recorded;
}

/** Сдвинуть порог DR: последствия показываются только у изменённых порогов. */
async function touchDr() {
  const user = userEvent.setup();
  const dr = screen.getByLabelText('DR не ниже');
  await user.clear(dr);
  await user.type(dr, '25');
}

describe('пороги', () => {
  it('нетронутые пороги не обещают перемен в базе', async () => {
    await openThresholds();

    expect(screen.getByText(/Пороги совпадают с действующими/)).toBeInTheDocument();
    expect(screen.queryByText('Выпадет из базы')).not.toBeInTheDocument();
  });

  it('показывает последствия до сохранения', async () => {
    await openThresholds();
    await touchDr();

    expect(await screen.findByText('13')).toBeInTheDocument();
    expect(screen.getByText('из них с ценой: 4')).toBeInTheDocument();
    // Домены без метрик считаются отдельно: их вердикт не изменится.
    expect(screen.getByText(/Ещё 6 доменов без метрик/)).toBeInTheDocument();
  });

  it('предупреждает, если выпадают доноры с полученной ценой', async () => {
    await openThresholds();
    await touchDr();

    expect(
      await screen.findByText('Среди выпавших есть доноры с полученной ценой'),
    ).toBeInTheDocument();
  });

  it('пока пороги не тронуты, сохранять нечего', async () => {
    await openThresholds();

    expect(screen.getByRole('button', { name: /Сохранить новой версией/ })).toBeDisabled();
  });

  it('сохранение заводит новую версию', async () => {
    const recorded = await openThresholds({
      'POST /api/settings/thresholds': { body: { ...CURRENT, version: 3, min_dr: 25 } },
    });
    const user = userEvent.setup();

    const dr = screen.getByLabelText('DR не ниже');
    await user.clear(dr);
    await user.type(dr, '25');
    await user.click(screen.getByRole('button', { name: /Сохранить новой версией/ }));

    await waitFor(() => {
      expect(screen.getByText(/сохранены как версия 3/)).toBeInTheDocument();
    });
    const saved = recorded.calls.find(
      (call) => call.path === '/api/settings/thresholds' && call.method === 'POST',
    );
    expect((saved?.body as { min_dr: number }).min_dr).toBe(25);
  });

  it('без права на пороги правка закрыта, а история видна', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: { ...OPERATOR, permissions: ['view'] } },
      'GET /api/settings/thresholds': { body: VIEW },
    });
    renderWith(<AppRoutes />, '/settings');

    expect(await screen.findByText('Править пороги не разрешено')).toBeInTheDocument();
    expect(screen.getByLabelText('DR не ниже')).toBeDisabled();
    expect(screen.getByText('№2')).toBeInTheDocument();
  });
});
