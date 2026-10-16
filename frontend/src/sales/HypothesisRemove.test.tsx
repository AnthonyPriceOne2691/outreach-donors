/**
 * «Удалить гипотезу» в окне правки (волна 15) — только пустую, на случай, если завели по ошибке.
 * Ответы сервера — записанные (`serve()`); сервер помнит удаление: список после него — без неё.
 *
 * Проверяется то, ради чего кнопка: что держит гипотезу, окно читает при открытии — непустая
 * видна, но закрыта, и под кнопкой строкой — слова сервера с числами; пустая — подтверждение у
 * самой кнопки с именем гипотезы, фокус на «Отмене»; удалённая уходит из списка и из адреса
 * раздела; 409 по гонке (лид завели в ту же секунду) — словами над полями, и кнопка
 * закрывается с новыми словами. Имена выдуманы.
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

const SCREEN_WAIT = { timeout: 5000 };
const ONLY_EMPTY =
  'Удаляется только пустая гипотеза, заведённая по ошибке; эту можно переименовать';
const HELD = `Удалить нельзя — её держат: лиды — 12, письма — 30. ${ONLY_EMPTY}`;

function card(id: number, name: string): HypothesisCard {
  return {
    id,
    name,
    description: null,
    created_at: '2026-10-10T09:00:00+00:00',
    leads: { new: 0, ready: 0, rejected: 0 },
    total: 0,
  };
}

const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

const EMPTY_LEADS = {
  rows: [],
  total: 0,
  page: 1,
  limit: 20,
  states: { new: 0, ready: 0, rejected: 0 },
  reasons: {},
};

type Route = Answer | ((call: Call) => Answer);

/** Сервер, который помнит удаление: `DELETE` — 204, список — уже без неё. */
function remembering(start: HypothesisCard[], held: string | null): Record<string, Route> {
  const known = [...start];
  const routes: Record<string, Route> = {
    'GET /api/sales/hypotheses': () => ({ body: { rows: [...known], total: known.length } }),
  };
  for (const row of start) {
    routes[`GET /api/sales/hypotheses/${row.id}/holds`] = {
      body: { hypothesis_id: row.id, holds: held === null ? {} : { leads: 12 }, held },
    };
    routes[`DELETE /api/sales/hypotheses/${row.id}`] = () => {
      known.splice(
        known.findIndex((item) => item.id === row.id),
        1,
      );
      return { status: 204 };
    };
  }
  return routes;
}

async function open(routes: Record<string, Route>, path = '/sales?tab=hypotheses') {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/kb': { body: KB },
    ...routes,
  });
  renderWith(<AppRoutes />, path);
  await screen.findByRole('button', { name: 'Править гипотезу «сайты EN»' }, SCREEN_WAIT);
  return { recorded, user: userEvent.setup() };
}

function deletes(recorded: Recorded): Call[] {
  return recorded.calls.filter((call) => call.method === 'DELETE');
}

async function openEdit(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Править гипотезу «сайты EN»' }));
  return screen.findByRole('dialog', { name: 'Гипотеза «сайты EN»' }, SCREEN_WAIT);
}

const EN = card(1, 'сайты EN');
const AR = card(2, 'مدارس اللغات');

describe('удаление гипотезы: только пустую', () => {
  it('пустая — подтверждение у кнопки с именем, фокус на «Отмене»; «Отмена» ничего не шлёт', async () => {
    const { recorded, user } = await open(remembering([EN, AR], null));

    const dialog = await openEdit(user);
    const remove = await within(dialog).findByRole(
      'button',
      { name: 'Удалить гипотезу' },
      SCREEN_WAIT,
    );
    expect(remove).toBeEnabled();
    await user.click(remove);

    const question = await screen.findByText(
      'Удалить гипотезу «сайты EN»? Лидов, прогонов сбора и писем у неё нет. Имя и описание останутся в журнале действий.',
      {},
      SCREEN_WAIT,
    );
    const popover = question.closest<HTMLElement>('.mantine-Popover-dropdown');
    expect(popover).not.toBeNull();
    const cancel = within(popover as HTMLElement).getByRole('button', { name: 'Отмена' });
    await waitFor(() => expect(cancel).toHaveFocus(), SCREEN_WAIT);
    await user.click(cancel);
    expect(deletes(recorded)).toEqual([]);
  });

  it('пустая — удалена: окно закрыто, в списке её нет, уведомление с именем', async () => {
    const { recorded, user } = await open(remembering([EN, AR], null));

    const dialog = await openEdit(user);
    await user.click(
      await within(dialog).findByRole('button', { name: 'Удалить гипотезу' }, SCREEN_WAIT),
    );
    await user.click(await screen.findByRole('button', { name: 'Удалить' }, SCREEN_WAIT));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), SCREEN_WAIT);
    expect(deletes(recorded).map((call) => call.path)).toEqual(['/api/sales/hypotheses/1']);
    await waitFor(
      () =>
        expect(
          screen.queryByRole('button', { name: 'Править гипотезу «сайты EN»' }),
        ).not.toBeInTheDocument(),
      SCREEN_WAIT,
    );
    expect(screen.getByRole('button', { name: 'Править гипотезу «مدارس اللغات»' })).toBeVisible();
    expect(
      await screen.findByText('Гипотеза «сайты EN» удалена', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
  });

  it('непустая — кнопка видна, но закрыта, под ней — что держит, словами сервера', async () => {
    const { recorded, user } = await open(remembering([EN, AR], HELD));

    const dialog = await openEdit(user);

    expect(await within(dialog).findByText(HELD, {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Удалить гипотезу' })).toBeDisabled();
    expect(deletes(recorded)).toEqual([]);
  });

  it('гонка: сервер отказал 409 — слова над полями, кнопка закрыта с новыми словами', async () => {
    const refused = `Гипотеза «сайты EN» не удалена — её держат: лиды — 1. ${ONLY_EMPTY}`;
    let read = 0;
    const { user } = await open({
      ...remembering([EN, AR], null),
      // Первое чтение — пустая; после отказа удаления окно перечитывает — уже держат.
      'GET /api/sales/hypotheses/1/holds': () => {
        read += 1;
        const held = read === 1 ? null : `Удалить нельзя — её держат: лиды — 1. ${ONLY_EMPTY}`;
        return { body: { hypothesis_id: 1, holds: held === null ? {} : { leads: 1 }, held } };
      },
      'DELETE /api/sales/hypotheses/1': { status: 409, body: { detail: refused } },
    });

    const dialog = await openEdit(user);
    await user.click(
      await within(dialog).findByRole('button', { name: 'Удалить гипотезу' }, SCREEN_WAIT),
    );
    await user.click(await screen.findByRole('button', { name: 'Удалить' }, SCREEN_WAIT));

    const alert = (await within(dialog).findByText('Гипотеза не удалена', {}, SCREEN_WAIT)).closest(
      '[role="alert"]',
    );
    expect(alert).toHaveTextContent(refused);
    await waitFor(
      () => expect(within(dialog).getByRole('button', { name: 'Удалить гипотезу' })).toBeDisabled(),
      SCREEN_WAIT,
    );
    expect(
      within(dialog).getByText(`Удалить нельзя — её держат: лиды — 1. ${ONLY_EMPTY}`),
    ).toBeInTheDocument();
  });

  it('удалённая была выбрана в разделе — уходит из адреса без строки «больше нет»', async () => {
    const { user } = await open(
      {
        ...remembering([EN, AR], null),
        'GET /api/sales/leads?hypothesis=2': { body: EMPTY_LEADS },
      },
      '/sales?tab=hypotheses&hypothesis=1',
    );

    const dialog = await openEdit(user);
    await user.click(
      await within(dialog).findByRole('button', { name: 'Удалить гипотезу' }, SCREEN_WAIT),
    );
    await user.click(await screen.findByRole('button', { name: 'Удалить' }, SCREEN_WAIT));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), SCREEN_WAIT);
    await screen.findByText('Гипотеза «сайты EN» удалена', {}, SCREEN_WAIT);
    // Список перечитан — удалённой в нём нет; строка «больше нет» за это время не встала бы
    // сразу, поэтому — ждать её секунду и не дождаться.
    await waitFor(
      () =>
        expect(
          screen.queryByRole('button', { name: 'Править гипотезу «сайты EN»' }),
        ).not.toBeInTheDocument(),
      SCREEN_WAIT,
    );
    await expect(screen.findByText(/больше нет/, {}, { timeout: 1000 })).rejects.toThrow();
  });
});
