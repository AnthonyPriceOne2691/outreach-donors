/**
 * Экран рассмотрения прогона: что видно и что уходит на сервер.
 *
 * Три обещания экрана: у каждого кандидата есть ярлык судьи (и «судья
 * не смотрел» — тоже ярлык), сомнительные скрыты со счётчиком, а не
 * выброшены, и решение человека уходит на сервер ровно тем списком,
 * который он отметил.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import type { Call } from '../test/server';
import { serve } from '../test/server';

const MACHINE = {
  intent: 'editorial_ads',
  recommendation: 'accept',
  decided_by: 'model',
  quote: 'Write for us: we accept guest posts on SaaS marketing',
  reason: null,
  source_url: 'https://martech.example.test/write-for-us',
  home_shop: [],
  home_reached: true,
  judged_at: '2026-09-23T17:00:00Z',
};

const SELLER = { answer: null, answered_at: null, price: null, currency: null };

function card(id: number, host: string, extra: Record<string, unknown> = {}) {
  return {
    candidate_id: id,
    domain_id: id * 10,
    host,
    status: 'pending',
    tier: 'likely',
    carried: false,
    decided_by: null,
    decided_at: null,
    note: null,
    dr: 55,
    org_traffic: 12000,
    geo: 'us',
    geo_top_share: 0.62,
    contact_status: null,
    found_by: ['saas blog write for us', 'martech blog submit article', 'saas seo strategy'],
    machine: MACHINE,
    seller: SELLER,
    ...extra,
  };
}

const VIEW = {
  run: { id: 18, country: 'us', keywords: 100, created_at: '2026-09-23T16:21:00Z' },
  rows: [
    card(1, 'martech.example.test'),
    card(2, 'unjudged.example.test', {
      tier: 'open',
      machine: { ...MACHINE, recommendation: null, intent: null, decided_by: null, quote: null },
    }),
  ],
  counts: { pending: 394, accepted: 0, rejected: 0 },
  hidden: 278,
};

const ACCURACY = {
  decided: 0,
  by_advice: {},
  by_layer: {},
  by_intent: {},
  unjudged: 0,
  asked_to_review: 0,
  auto_accept_ready: false,
  auto_accept_precision: 0.95,
  auto_accept_min_decisions: 200,
};

const PENDING = 'GET /api/review/runs/18?status=pending';

async function openReview(routes: Record<string, unknown> = {}, who = ADMIN) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    [PENDING]: { body: VIEW },
    'GET /api/review/accuracy': { body: ACCURACY },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/runs/18/review');
  await screen.findByRole('heading', { name: 'Прогон №18: рассмотрение' });
  return recorded;
}

describe('рассмотрение прогона', () => {
  it('у каждого кандидата ярлык судьи, и «не смотрел» — тоже ярлык', async () => {
    await openReview();

    const judged = screen.getByText('martech.example.test').closest('tr') as HTMLElement;
    expect(within(judged).getByText('площадка')).toBeInTheDocument();
    expect(within(judged).getByText('издание')).toBeInTheDocument();
    expect(within(judged).getByText(/we accept guest posts/)).toBeInTheDocument();

    const blind = screen.getByText('unjudged.example.test').closest('tr') as HTMLElement;
    expect(within(blind).getByText('судья не смотрел')).toBeInTheDocument();
  });

  it('видно, по каким ключам нашёлся домен', async () => {
    await openReview();

    const row = screen.getByText('martech.example.test').closest('tr') as HTMLElement;
    expect(within(row).getByText('saas blog write for us')).toBeInTheDocument();
    expect(within(row).getByText('и ещё 1')).toBeInTheDocument();
  });

  it('сомнительные скрыты со счётчиком, а не выброшены', async () => {
    const recorded = await openReview({
      'GET /api/review/runs/18?status=pending&show_doubtful=true': { body: VIEW },
    });
    const user = userEvent.setup();

    await user.click(screen.getByLabelText('Показать сомнительные (скрыто 278)'));

    expect(recorded.calls.some((call: Call) => call.path.endsWith('show_doubtful=true'))).toBe(
      true,
    );
  });

  it('решение пачкой уходит ровно теми, кого отметил человек', async () => {
    const recorded = await openReview({
      'POST /api/review/runs/18/decide': {
        body: { changed: 2, accepted: 2, contacts_job_id: 'j' },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByLabelText('Выбрать martech.example.test'));
    await user.click(screen.getByLabelText('Выбрать unjudged.example.test'));
    await user.click(screen.getByRole('button', { name: 'Принять выбранные' }));

    const call = recorded.calls.find((one: Call) => one.path.endsWith('/decide'));
    expect(call?.body).toEqual({ candidate_ids: [1, 2], decision: 'accepted' });
  });

  it('отказ по одной строке уходит одним кандидатом', async () => {
    const recorded = await openReview({
      'POST /api/review/runs/18/decide': {
        body: { changed: 1, accepted: 0, contacts_job_id: null },
      },
    });
    const user = userEvent.setup();

    const row = screen.getByText('martech.example.test').closest('tr') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: 'Отклонить' }));

    const call = recorded.calls.find((one: Call) => one.path.endsWith('/decide'));
    expect(call?.body).toEqual({ candidate_ids: [1], decision: 'rejected' });
  });

  it('готовность к автоприёму названа вместе с условием', async () => {
    await openReview();

    expect(screen.getByText('рано')).toBeInTheDocument();
    expect(screen.getByText('нужно ≥ 95% на ≥ 200')).toBeInTheDocument();
  });

  it('без права решать кнопок нет, а очередь видна', async () => {
    const viewer = { ...ADMIN, permissions: ['view'] };
    await openReview({}, viewer as typeof ADMIN);

    expect(screen.queryByRole('button', { name: 'Принять' })).not.toBeInTheDocument();
    expect(screen.getAllByText('ждёт решения')).toHaveLength(2);
  });
});

describe('передумать', () => {
  const decided = (status: 'accepted' | 'rejected') => ({
    ...VIEW,
    rows: [card(1, 'martech.example.test', { status, decided_by: 'оператор@site.com' })],
    counts: {
      pending: 393,
      accepted: status === 'accepted' ? 1 : 0,
      rejected: status === 'rejected' ? 1 : 0,
    },
  });

  it.each(['accepted', 'rejected'] as const)(
    'со вкладки «%s» решение возвращается в «предложен»',
    async (status) => {
      const recorded = await openReview({
        [`GET /api/review/runs/18?status=${status}`]: { body: decided(status) },
        'POST /api/review/runs/18/decide': {
          body: { changed: 1, accepted: 0, contacts_job_id: null },
        },
      });
      const user = userEvent.setup();

      await user.click(
        screen.getByText(new RegExp(`^${status === 'accepted' ? 'Приняты' : 'Отклонены'} —`)),
      );
      const row = (await screen.findByText('martech.example.test')).closest('tr') as HTMLElement;
      await user.click(within(row).getByRole('button', { name: 'Вернуть' }));

      const call = recorded.calls.find((one: Call) => one.path.endsWith('/decide'));
      expect(call?.body).toEqual({ candidate_ids: [1], decision: 'pending' });
    },
  );
});
