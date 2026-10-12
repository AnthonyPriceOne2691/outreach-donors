/**
 * «Диалоги» и «Диалоги продаж» — один экран двумя частями (`?stage=sales`, просьба
 * Anthony 11.10.2026). Проверяется: каждая часть — только свои переписки и свои числа;
 * сброс фильтров и вкладки части не теряют; открытая переписка ведёт часть за собой;
 * без права «Продажи» часть продаж из адреса не сужает.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { Me } from '../api/types';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

function thread(id: number, host: string, stage: string, state: string) {
  return {
    id,
    host,
    contact_email: `hi@${host}`,
    campaign: 'Демонстрация',
    stage,
    state,
    messages_sent: 1,
    last_event_at: '2026-10-11T10:00:00+00:00',
    last_reply_at: null,
    price_white: null,
    price_grey: null,
    currency: null,
  };
}

const THREADS = [
  thread(3, 'donor.example.test', 'donors', 'needs_review'),
  thread(4, 'brand.example.test', 'advertisers', 'lead'),
  thread(7, 'lead.example.test', 'sales', 'sales_pending'),
];

function view(card: ReturnType<typeof thread>) {
  return { card, letters: [], incoming: [], corridor: { min: 0.15, max: 0.25 } };
}

function Where() {
  const location = useLocation();
  return <output data-testid="where">{`${location.pathname}${location.search}`}</output>;
}

async function open(path: string, me: Me = ADMIN, threads = THREADS) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: me },
    'GET /api/threads': { body: threads },
    ...Object.fromEntries(
      THREADS.map((card) => [`GET /api/threads/${card.id}`, { body: view(card) }]),
    ),
    'GET /api/replies/calibration': { body: { versions: [] } },
    'GET /api/replies/unbound?page=1': { body: { rows: [], total: 0, page: 1, limit: 20 } },
  });
  renderWith(
    <>
      <AppRoutes />
      <Where />
    </>,
    path,
  );
  await screen.findByRole('navigation', { name: 'Разделы' });
}

const where = () => screen.getByTestId('where');

describe('части списка диалогов', () => {
  it('«Диалоги продаж» — только переписки продаж, заголовок и число — части', async () => {
    await open('/threads?stage=sales');

    expect(await screen.findByRole('heading', { name: 'Диалоги продаж' })).toBeInTheDocument();
    expect(await screen.findByText('lead.example.test')).toBeInTheDocument();
    expect(screen.queryByText('donor.example.test')).not.toBeInTheDocument();
    expect(screen.queryByText('brand.example.test')).not.toBeInTheDocument();
    expect(screen.getByText('всего 1')).toBeInTheDocument();
  });

  it('«Диалоги» — доноры и рекламодатели, без продаж', async () => {
    await open('/threads');

    expect(await screen.findByText('donor.example.test')).toBeInTheDocument();
    expect(screen.getByText('brand.example.test')).toBeInTheDocument();
    expect(screen.queryByText('lead.example.test')).not.toBeInTheDocument();
    expect(screen.getByText('всего 2')).toBeInTheDocument();
  });

  it('сброс фильтров часть не сбрасывает: диалоги продаж остаются диалогами продаж', async () => {
    const user = userEvent.setup();
    await open('/threads?stage=sales&search=nothing-like-this');

    await user.click(await screen.findByRole('button', { name: 'Сбросить фильтры' }));

    await waitFor(() => expect(where()).toHaveTextContent(/^\/threads\?stage=sales$/));
    expect(await screen.findByText('lead.example.test')).toBeInTheDocument();
  });

  it('вкладка «Не привязаны» и обратно — в той же части', async () => {
    const user = userEvent.setup();
    await open('/threads?stage=sales');
    await screen.findByText('lead.example.test');

    await user.click(await screen.findByRole('radio', { name: 'Не привязаны — 0' }));
    expect(where()).toHaveTextContent('/threads?stage=sales&tab=unbound');
    await user.click(await screen.findByRole('radio', { name: /^Диалоги/ }));

    await waitFor(() => expect(where()).toHaveTextContent(/^\/threads\?stage=sales$/));
  });

  it('пустая часть продаж говорит, где заводится переписка продаж', async () => {
    await open('/threads?stage=sales', ADMIN, THREADS.slice(0, 2));

    expect(await screen.findByText('Диалогов ещё нет.')).toBeInTheDocument();
    expect(screen.getByText(/очередь писем собирают в разделе «Продажи»/)).toBeInTheDocument();
  });

  it('без права «Продажи» часть продаж из адреса не сужает — список без неё', async () => {
    const noSales: Me = { ...OPERATOR, permissions: ['prices', 'run', 'settings', 'view'] };
    await open('/threads?stage=sales', noSales, THREADS.slice(0, 2));

    expect(await screen.findByRole('heading', { name: 'Диалоги' })).toBeInTheDocument();
    expect(await screen.findByText('donor.example.test')).toBeInTheDocument();
  });
});

describe('открытая переписка ведёт часть за собой', () => {
  it('ссылка без части на переписку продаж — «Диалоги продаж»', async () => {
    // Так ведут уведомления продаж: `/threads/{id}` без части (`sales/handoff_text.py`).
    await open('/threads/7');

    await waitFor(() => expect(where()).toHaveTextContent('/threads/7?stage=sales'));
  });

  it('переписка донора под частью продаж — обратно в «Диалоги»', async () => {
    await open('/threads/3?stage=sales&state=needs_review');

    await waitFor(() => expect(where()).toHaveTextContent(/^\/threads\/3\?state=needs_review$/));
  });
});
