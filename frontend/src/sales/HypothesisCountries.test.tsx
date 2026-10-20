/**
 * Поле гипотезы «страны компаний» в окне гипотезы (судья сегмента v4): переключатель «Компании из
 * других стран» и «Не пишем компаниям из». Ответы сервера — записанные (`serve()`); страны и имена
 * выдуманы, разных письменностей.
 *
 * Проверяется то, ради чего поле: оно — в окне правки гипотезы (новая заводится с «На проверку» и
 * пустым списком); поправили — уходят только поправленные части; список стран сервер отдаёт кодами,
 * а названия — у браузера; коды спрашиваются, только когда выбор открыли.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesisCard } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';
import { countryLabel } from './HypothesisCountries';

const SCREEN_WAIT = { timeout: 5000 };
const COUNTRIES = 'GET /api/sales/countries';
const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

type Route = Answer | ((call: Call) => Answer);
type Field = { other_countries?: 'review' | 'accept'; excluded_countries?: string[] };

function card(id: number, name: string, field: Field = {}): HypothesisCard {
  return {
    id,
    name,
    description: 'Пекарни: пекут сами, продают на своём сайте.',
    created_at: '2026-10-10T09:00:00+00:00',
    leads: { new: 0, ready: 0, rejected: 0 },
    total: 0,
    ...field,
  };
}

const BAKERIES = card(1, 'пекарни', { other_countries: 'review', excluded_countries: [] });
const CLINICS = card(2, 'клиники', { other_countries: 'accept', excluded_countries: ['CL'] });

function routes(rows: HypothesisCard[]): Record<string, Route> {
  const found: Record<string, Route> = {
    'GET /api/sales/hypotheses': { body: { rows, total: rows.length } },
    [COUNTRIES]: { body: { codes: ['CL', 'JP', 'PT', 'KE'] } },
    'POST /api/sales/hypotheses': (call) => ({
      status: 201,
      body: { ...card(9, 'новая'), ...(call.body as object) },
    }),
  };
  for (const row of rows) {
    found[`GET /api/sales/hypotheses/${row.id}/holds`] = {
      body: { hypothesis_id: row.id, holds: {}, held: null },
    };
    found[`PATCH /api/sales/hypotheses/${row.id}`] = (call) => ({
      body: {
        hypothesis_id: row.id,
        name: row.name,
        description: row.description,
        ...(call.body as object),
      },
    });
  }
  return found;
}

async function open(rows: HypothesisCard[]) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/kb': { body: KB },
    ...routes(rows),
  });
  renderWith(<AppRoutes />, '/sales?tab=hypotheses');
  await screen.findByRole('button', { name: 'Править гипотезу «пекарни»' }, SCREEN_WAIT);
  return { recorded, user: userEvent.setup() };
}

async function openEdit(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole('button', { name: `Править гипотезу «${name}»` }));
  return screen.findByRole('dialog', { name: `Гипотеза «${name}»` }, SCREEN_WAIT);
}

function sent(recorded: Recorded, method: string): unknown[] {
  return recorded.calls.filter((call) => call.method === method).map((call) => call.body);
}

describe('поле «страны компаний» в окне гипотезы', () => {
  it('у гипотезы — её положение и страны «не пишем» названиями браузера', async () => {
    const { recorded, user } = await open([BAKERIES, CLINICS]);

    const dialog = await openEdit(user, 'клиники');

    const switcher = within(dialog).getByRole('radiogroup', { name: 'Компании из других стран' });
    expect(within(switcher).getByRole('radio', { name: 'Подходят' })).toBeChecked();
    expect(within(dialog).getByText(countryLabel('CL'))).toBeInTheDocument();
    expect(countryLabel('CL')).toBe('Чили · CL');
    // Список кодов не нужен, пока выбор не открыли.
    expect(recorded.calls.some((call) => `${call.method} ${call.path}` === COUNTRIES)).toBe(false);
  });

  it('поправили переключатель и страну — уходят только они; имя и описание не трогаются', async () => {
    const { recorded, user } = await open([BAKERIES, CLINICS]);

    const dialog = await openEdit(user, 'пекарни');
    await user.click(within(dialog).getByRole('radio', { name: 'Подходят' }));
    await user.click(within(dialog).getByRole('textbox', { name: 'Не пишем компаниям из' }));
    await user.click(await screen.findByRole('option', { name: countryLabel('KE') }, SCREEN_WAIT));
    await user.click(within(dialog).getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(sent(recorded, 'PATCH')).toHaveLength(1), SCREEN_WAIT);
    expect(sent(recorded, 'PATCH')).toEqual([
      { other_countries: 'accept', excluded_countries: ['KE'] },
    ]);
  });

  it('новая гипотеза — без поля: заводится с «На проверку» и пустым списком', async () => {
    const { recorded, user } = await open([BAKERIES]);

    await user.click(screen.getByRole('button', { name: 'Новая гипотеза' }));
    const dialog = await screen.findByRole('dialog', { name: 'Новая гипотеза' }, SCREEN_WAIT);
    expect(
      within(dialog).queryByRole('radiogroup', { name: 'Компании из других стран' }),
    ).toBeNull();
    await user.type(within(dialog).getByRole('textbox', { name: /Имя/ }), '料理教室');
    await user.click(within(dialog).getByRole('button', { name: 'Завести' }));

    await waitFor(() => expect(sent(recorded, 'POST')).toHaveLength(1), SCREEN_WAIT);
    expect(sent(recorded, 'POST')).toEqual([{ name: '料理教室', description: null }]);
  });
});
