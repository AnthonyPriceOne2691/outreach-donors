/**
 * Состояние диалога, которого экран ещё не знает: сервер новее экрана.
 *
 * Новое состояние приходит с сервером раньше, чем экран получает его подпись.
 * Список и карточка показывают его кодом, а не падают целиком на `undefined`
 * (`api/labels.threadState`).
 */

import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const NEW_STATE = 'state_of_a_newer_server';

const CARD = {
  id: 7,
  host: 'fresh.example.test',
  contact_email: 'editor@fresh.example.test',
  campaign: 'Демонстрация',
  stage: 'donors',
  state: NEW_STATE,
  messages_sent: 1,
  last_event_at: '2026-10-05T10:00:00+00:00',
  last_reply_at: null,
  price_white: null,
  price_grey: null,
  currency: null,
};

function signedIn(routes: Parameters<typeof serve>[0]) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({ 'GET /api/auth/me': { body: ADMIN }, ...routes });
}

describe('состояние, которого экран ещё не знает', () => {
  it('список диалогов показывает его кодом, а не падает', async () => {
    signedIn({
      'GET /api/threads': { body: [CARD] },
      'GET /api/replies/calibration': { body: { versions: [] } },
      'GET /api/replies/unbound?page=1': { body: { rows: [], total: 0, page: 1, limit: 20 } },
    });

    renderWith(<AppRoutes />, '/threads');

    const row = (await screen.findByText('fresh.example.test')).closest('a')!;
    expect(within(row).getByText(NEW_STATE)).toBeInTheDocument();
  });

  it('карточка диалога — тоже', async () => {
    signedIn({
      'GET /api/threads/7': {
        body: { card: CARD, letters: [], incoming: [], corridor: { min: 0.15, max: 0.25 } },
      },
    });

    renderWith(<AppRoutes />, '/threads/7');

    await screen.findByRole('heading', { name: 'fresh.example.test' });
    expect(screen.getByText(NEW_STATE)).toBeInTheDocument();
  });
});
