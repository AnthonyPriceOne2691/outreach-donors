/**
 * «Не пишем», а рекламодатель уже ответил (ревью 12.10.2026): решение снимает оффер
 * и добивки, а переписку — нет, и экран говорит это вслух. Предупреждение жёлтое
 * и не гаснет само: оно просит посмотреть «Диалоги», а не отчитывается.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, CRAWL_ROUTES, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const CANDIDATE = {
  id: 12,
  donor_host: 'donor.example.test',
  target_root: 'advertiser.example.test',
  points: 5,
  verdict: 'bought' as const,
  reasons: ['rel=sponsored +5'],
  links: 1,
  pages: 1,
  best_page_url: 'https://donor.example.test/post/1',
  best_anchor: 'Brand',
  confirmed: null,
  decided_by: null,
  decided_at: null,
};

const WARNING =
  'advertiser.example.test уже ответил — переписка идёт в «Диалогах», и «не пишем» её не ' +
  'останавливает: сняты только оффер и добивки';

async function decideNo(answer: Record<string, unknown>) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    ...CRAWL_ROUTES,
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/advertisers/niche?page=1': {
      body: { rows: [], waiting: 0, total: 0, page: 1, limit: 20 },
    },
    'GET /api/advertisers': {
      body: { rows: [CANDIDATE], waiting: 1, counts: { pending: 1 } },
    },
    'POST /api/advertisers/12/decide': { body: { ...CANDIDATE, confirmed: false, ...answer } },
  });
  renderWith(<AppRoutes />, '/advertisers');
  await screen.findByText('advertiser.example.test');
  await userEvent.setup().click(screen.getByRole('button', { name: 'Не пишем' }));
  await screen.findByText('advertiser.example.test: не пишем');
}

describe('«не пишем» при идущей переписке', () => {
  it('рекламодатель уже ответил — экран это говорит', async () => {
    await decideNo({ warning: WARNING });

    expect(await screen.findByText(WARNING)).toBeInTheDocument();
  });

  it('ответа не было — лишнего не говорит', async () => {
    await decideNo({ warning: null });

    expect(screen.queryByText(/уже ответил/)).not.toBeInTheDocument();
  });
});
