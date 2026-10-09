/**
 * Бизнесы ниши из выдачи на экране рекламодателей: откуда взялся, кто
 * сказал «продаёт своё», решение человека и сбор из старого прогона.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { NicheCard } from '../api/niche';
import { ADMIN, CRAWL_ROUTES, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const BOOKIE: NicheCard = {
  id: 7,
  host: 'bookie.example.test',
  run_id: 21,
  keywords: ['sports betting', 'odds'],
  country: 'de',
  quote: 'Place your bets on football',
  intent_by: 'judge',
  confirmed: null,
  decided_by: null,
  decided_at: null,
};

async function openAdvertisers(routes: Record<string, unknown> = {}, user: unknown = ADMIN) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    // Панели обхода и перевода на том же экране — пустые: проверяется карточка ниши.
    ...CRAWL_ROUTES,
    'GET /api/auth/me': { body: user },
    'GET /api/advertisers': { body: { rows: [], waiting: 0, counts: {} } },
    'GET /api/advertisers/niche': { body: { rows: [BOOKIE], waiting: 1 } },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/advertisers');
  await screen.findByText('bookie.example.test');
  return recorded;
}

describe('бизнесы ниши из выдачи', () => {
  it('показывает, откуда бизнес и кто сказал «продаёт своё»', async () => {
    await openAdvertisers();

    expect(screen.getByText('прогон №21 · sports betting, odds · DE')).toBeInTheDocument();
    expect(screen.getByText('продаёт своё — судья')).toBeInTheDocument();
    expect(screen.getByText('«Place your bets on football»')).toBeInTheDocument();
    expect(screen.getByText(/Ждут решения: 1\./)).toBeInTheDocument();
  });

  it('«Пишем» уходит на сервер решением человека', async () => {
    const recorded = await openAdvertisers({
      'POST /api/advertisers/niche/7/decide': { body: { ...BOOKIE, confirmed: true } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Пишем' }));

    await waitFor(() => {
      const sent = recorded.calls.find((call) => call.path === '/api/advertisers/niche/7/decide');
      expect(sent?.body).toEqual({ write: true });
    });
  });

  it('сбор из старого прогона говорит, сколько нашлось', async () => {
    const recorded = await openAdvertisers({
      'POST /api/advertisers/niche/collect?run_id=18': {
        body: { run_id: 18, found: 4, added: 3 },
      },
    });
    const user = userEvent.setup();

    await user.type(screen.getByLabelText('Собрать из прогона №'), '18');
    await user.click(screen.getByRole('button', { name: 'Собрать' }));

    expect(await screen.findByText('Прогон №18: бизнесов ниши 4, новых 3.')).toBeInTheDocument();
    expect(recorded.calls.some((call) => call.path.endsWith('collect?run_id=18'))).toBe(true);
  });

  it('без права решать кнопок и сбора нет', async () => {
    await openAdvertisers({}, { ...OPERATOR, permissions: ['view'] });

    expect(screen.queryByRole('button', { name: 'Пишем' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Собрать из прогона №')).not.toBeInTheDocument();
  });
});
