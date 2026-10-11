/**
 * Правка лида на вкладке «Лиды»: окно из строки, что уходит на сервер, что сказано после.
 *
 * Проверяется то, ради чего правка: имя и язык лида уходят телом `PATCH` (пустое — `null`,
 * «не задано»), письмо в очереди после правки — сказано словами, отказ сервера — над формой,
 * введённое остаётся. Без права отправки окно — смотреть. Ответы сервера записанные (`serve()`).
 */

import { notifications } from '@mantine/notifications';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { LeadChanged } from '../api/salesLeads';
import type { HypothesesView, KbView, LeadCard, LeadsView } from '../api/salesTypes';
import type { Me } from '../api/types';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Recorded } from '../test/server';

const SCREEN_WAIT = { timeout: 5000 };

const HYPOTHESES: HypothesesView = {
  rows: [
    {
      id: 4,
      name: 'своя база',
      description: null,
      created_at: '2026-10-02T08:00:00+00:00',
      leads: { new: 0, ready: 1, rejected: 0 },
      total: 1,
    },
  ],
  total: 1,
  module_enabled: true,
};

/** Лид из «пишите другому»: имени нет, языка нет — ему не уйдёт ни одно письмо. */
const REFERRED: LeadCard = {
  id: 17,
  email: 'olga@vega.example.test',
  name: null,
  position: null,
  company: 'Vega',
  host: 'vega.example.test',
  country: 'de',
  timezone: null,
  language: null,
  hypothesis_id: 4,
  hypothesis: 'своя база',
  source: 'referral',
  status: 'ready',
  rejection_reason: null,
  cleaning_note: null,
  verification_status: 'fixture:valid',
  created_at: '2026-10-05T09:15:00+00:00',
};

const LEADS: LeadsView = {
  rows: [REFERRED],
  total: 1,
  page: 1,
  limit: 20,
  states: { new: 0, ready: 1, rejected: 0 },
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

const PATCH = 'PATCH /api/sales/leads/17';

async function openLeads(routes: Record<string, Answer> = {}, who: Me = ADMIN): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/leads': { body: LEADS },
    'GET /api/sales/kb': { body: KB },
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales');
  await screen.findByText('olga@vega.example.test', {}, SCREEN_WAIT);
  return recorded;
}

function saved(extra: Partial<LeadChanged> = {}): LeadChanged {
  return { ...REFERRED, name: 'Olga Weber', changed: ['name'], rebuild: false, ...extra };
}

describe('правка лида', () => {
  it('имя и язык уходят телом правки, пустое — «не задано», и лиды перечитываются', async () => {
    const recorded = await openLeads({
      [PATCH]: { body: saved({ language: 'en', changed: ['name', 'language'], rebuild: true }) },
    });
    const show = vi.spyOn(notifications, 'show');
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Править лида olga@vega.example.test' }));
    const dialog = await screen.findByRole('dialog', {}, SCREEN_WAIT);
    const save = within(dialog).getByRole('button', { name: 'Сохранить' });
    expect(save).toBeDisabled(); // ничего не правили — сохранять нечего
    await user.type(within(dialog).getByRole('textbox', { name: 'Имя' }), 'Olga Weber');
    await user.click(within(dialog).getByRole('textbox', { name: 'Язык письма' }));
    await user.click(
      await screen.findByRole('option', { name: 'английский (en)', hidden: true }, SCREEN_WAIT),
    );
    await user.click(save);

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull(), SCREEN_WAIT);
    const [call] = recorded.calls.filter((made) => made.method === 'PATCH');
    expect(call?.body).toEqual({
      name: 'Olga Weber',
      position: null,
      company: 'Vega',
      language: 'en',
      country: 'de',
      timezone: null,
    });
    expect(show).toHaveBeenCalledWith(
      expect.objectContaining({
        message:
          'Лид olga@vega.example.test сохранён. Письмо в очереди соберёт заново следующая сборка.',
      }),
    );
    await waitFor(
      () =>
        expect(
          recorded.calls.filter((made) => made.path === '/api/sales/leads').length,
        ).toBeGreaterThan(1),
      SCREEN_WAIT,
    );
  });

  it('отказ сервера — над формой словами, окно и набранное остаются', async () => {
    const refused = 'страна «Нарния» не распознана: ждём код или название — de, Germany, Германия';
    await openLeads({ [PATCH]: { status: 400, body: { detail: refused } } });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Править лида olga@vega.example.test' }));
    const dialog = await screen.findByRole('dialog', {}, SCREEN_WAIT);
    const country = within(dialog).getByRole('textbox', { name: 'Страна' });
    await user.clear(country);
    await user.type(country, 'Нарния');
    await user.click(within(dialog).getByRole('button', { name: 'Сохранить' }));

    expect(await within(dialog).findByText(refused, {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(country).toHaveValue('Нарния');
  });

  it('без права отправки — «Открыть»: окно смотреть, записать нечем, почему — строкой', async () => {
    const recorded = await openLeads({}, OPERATOR);
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Открыть лида olga@vega.example.test' }));
    const dialog = await screen.findByRole('dialog', {}, SCREEN_WAIT);

    expect(within(dialog).getByRole('textbox', { name: 'Имя' })).toBeDisabled();
    expect(within(dialog).getByRole('button', { name: 'Сохранить' })).toBeDisabled();
    expect(
      within(dialog).getByText(
        'Правит лида тот, у кого есть право отправки писем. Смотреть можно всем.',
      ),
    ).toBeInTheDocument();
    expect(recorded.calls.some((made) => made.method === 'PATCH')).toBe(false);
  });
});
