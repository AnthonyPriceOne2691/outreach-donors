/**
 * Лид «готов», чей сайт в работе у другого направления: письма ему не собираются, пока сайт не
 * освободится, — на вкладке «Лиды» он назван значком и словами сервера (`elsewhere`). Без них он
 * неотличим от лида, которому письмо просто ещё не собрали. Ответы записанные.
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
const WAITS =
  'сайт в работе у другого направления: доноры — лид ждёт: одному сайту письма в двух ролях не ' +
  'уходят; письма того направления не дойдут — сборка напишет ему снова';

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 9,
      name: 'сайты EN',
      description: null,
      created_at: '2026-10-03T07:45:00+00:00',
      leads: { new: 0, ready: 2, rejected: 0 },
      total: 2,
    },
  ],
  total: 1,
  module_enabled: true,
};

const LEAD: Omit<LeadCard, 'id' | 'email' | 'host'> = {
  name: null,
  position: null,
  company: 'Sigma',
  country: 'de',
  timezone: 'Europe/Berlin',
  language: 'en',
  hypothesis_id: 9,
  hypothesis: 'сайты EN',
  source: 'import',
  status: 'ready',
  rejection_reason: null,
  cleaning_note: null,
  verification_status: 'hunter:valid',
  unproven: null,
  created_at: '2026-10-03T08:00:00+00:00',
};

const LEADS: LeadsView = {
  rows: [
    {
      ...LEAD,
      id: 31,
      email: 'mila@sigma.example.test',
      host: 'sigma.example.test',
      elsewhere: WAITS,
    },
    { ...LEAD, id: 32, email: 'olaf@tau.example.test', host: 'tau.example.test', elsewhere: null },
  ],
  total: 2,
  page: 1,
  limit: 20,
  states: { new: 0, ready: 2, rejected: 0 },
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

describe('сайт лида в работе у другого направления', () => {
  it('лид назван значком и словами, соседний «готов» — вердиктом', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/sales/hypotheses': { body: HYPOTHESES },
      'GET /api/sales/leads?hypothesis=9': { body: LEADS },
      'GET /api/sales/kb': { body: KB },
    });
    renderWith(<AppRoutes />, '/sales?hypothesis=9');
    const user = userEvent.setup();

    const waits = (await screen.findByText('mila@sigma.example.test', {}, SCREEN_WAIT)).closest(
      'tr',
    );
    const free = screen.getByText('olaf@tau.example.test').closest('tr');
    if (waits === null || free === null) throw new Error('строк лидов нет');
    expect(within(waits).getByText('сайт занят')).toBeInTheDocument();
    await user.click(within(waits).getByRole('button', { name: 'Почему лид ждёт' }));
    expect(await screen.findByText(WAITS, {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(within(free).queryByText('сайт занят')).not.toBeInTheDocument();
    expect(within(free).getByText('вердикт hunter:valid')).toBeInTheDocument();
  });
});
