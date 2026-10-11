/**
 * Адрес проверял выдуманный проверяльщик, а задан живой: лид назван на вкладке «Лиды», и кнопка
 * очистки его ждёт. Слова и числа — сервера (`unproven`, `recheck`); ответы записанные.
 */

import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesesView, KbView, LeadCard, LeadsView } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const SCREEN_WAIT = { timeout: 5000 };
const MADE_UP = 'адрес проверен тестовым проверяльщиком — очистите базу заново';

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 9,
      name: 'база с пилота',
      description: null,
      created_at: '2026-10-03T07:45:00+00:00',
      leads: { new: 1, ready: 2, rejected: 0 },
      total: 3,
      recheck: 2,
    },
  ],
  total: 1,
  module_enabled: true,
};

const PILOT: LeadCard = {
  id: 31,
  email: 'mila@sigma.example.test',
  name: 'Mila',
  position: null,
  company: 'Sigma',
  host: 'sigma.example.test',
  country: 'de',
  timezone: 'Europe/Berlin',
  language: 'en',
  hypothesis_id: 9,
  hypothesis: 'база с пилота',
  source: 'import',
  status: 'ready',
  rejection_reason: null,
  cleaning_note: null,
  verification_status: 'fixture:valid',
  unproven: MADE_UP,
  created_at: '2026-10-03T08:00:00+00:00',
};

const LEADS: LeadsView = {
  rows: [PILOT],
  total: 1,
  page: 1,
  limit: 20,
  states: { new: 1, ready: 2, rejected: 0 },
  reasons: { duplicate: 0 },
};

const KB: KbView = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
  gaps: [],
};

describe('выдуманный вердикт при живом проверяльщике', () => {
  it('лид назван значком и словами, очистка ждёт и его', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/sales/hypotheses': { body: HYPOTHESES },
      'GET /api/sales/leads?hypothesis=9': { body: LEADS },
      'GET /api/sales/kb': { body: KB },
    });
    renderWith(<AppRoutes />, '/sales?hypothesis=9');
    const user = userEvent.setup();

    const row = (await screen.findByText('mila@sigma.example.test', {}, SCREEN_WAIT)).closest('tr');
    if (row === null) throw new Error('строки лида нет');
    expect(within(row).getByText('проверить заново')).toBeInTheDocument();
    await user.click(within(row).getByRole('button', { name: 'Что сказала очистка' }));
    expect(await screen.findByText(MADE_UP, {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(
      screen.getByText(
        '3 лида ждут очистки: из них 2 проверены тестовым проверяльщиком — очистка проверит адрес по-настоящему',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Очистить' })).toBeEnabled();
  });
});
