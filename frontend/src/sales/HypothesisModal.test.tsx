/**
 * Окно «Новая гипотеза» — на вкладке «Гипотезы» и в мастере загрузки (до него — только
 * команда консоли, и мастер без гипотезы не шёл). Ответы сервера — записанные (`serve()`);
 * сервер помнит заведённую: список после заведения — уже с ней. Имена выдуманы.
 *
 * Проверяется то, ради чего окно: пустое имя — словами у поля и без запроса; отказ сервера —
 * словами над полями, набранное на месте; заведённая — в таблице сразу, а в мастере — сразу
 * выбрана; кнопка не залитая — залитая на вкладке одна, «Загрузить базу».
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
const ADD = '/api/sales/hypotheses';
const NO_NAME = 'Впишите имя — по нему гипотезу выбирают при загрузке базы';

function card(id: number, name: string, description: string | null = null): HypothesisCard {
  return {
    id,
    name,
    description,
    created_at: '2026-10-10T09:00:00+00:00',
    leads: { new: 0, ready: 0, rejected: 0 },
    total: 0,
  };
}

const EN = card(1, 'сайты EN', 'редакции и блоги');

const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

type Route = Answer | ((call: Call) => Answer);

/** Сервер, который помнит заведённую: `POST` — 201 и карточка, список — уже с ней. */
function remembering(start: HypothesisCard[], made: HypothesisCard): Record<string, Route> {
  const known = [...start];
  return {
    'GET /api/sales/hypotheses': () => ({ body: { rows: [...known], total: known.length } }),
    [`POST ${ADD}`]: () => {
      known.push(made);
      return { status: 201, body: made };
    },
  };
}

async function open(path: string, routes: Record<string, Route>, ready: string) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/kb': { body: KB },
    ...routes,
  });
  renderWith(<AppRoutes />, path);
  await screen.findByText(ready, {}, SCREEN_WAIT);
  return { recorded, user: userEvent.setup() };
}

function adds(recorded: Recorded): Call[] {
  return recorded.calls.filter((call) => call.method === 'POST' && call.path === ADD);
}

/** Залитая ли кнопка: у залитой нет другого вида (`data-variant` — нет или `filled`). */
function isFilled(button: HTMLElement): boolean {
  const variant = button.getAttribute('data-variant');
  return variant === null || variant === 'filled';
}

async function openDialog(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  await user.click(screen.getByRole('button', { name: 'Новая гипотеза' }));
  return screen.findByRole('dialog', { name: 'Новая гипотеза' }, SCREEN_WAIT);
}

describe('новая гипотеза: вкладка «Гипотезы»', () => {
  it('кнопка — в строке вкладок за «Загрузить базу», не залитая: залитая на вкладке одна', async () => {
    await open('/sales?tab=hypotheses', remembering([EN], card(2, 'x')), 'сайты EN');

    const add = screen.getByRole('button', { name: 'Новая гипотеза' });
    const upload = screen.getByRole('link', { name: 'Загрузить базу' });
    expect(isFilled(add)).toBe(false);
    expect(isFilled(upload)).toBe(true);
    // Одна строка действий сразу за переключателем: загрузка первой, гипотеза за ней.
    expect(add.closest('.salesTabs')).toBe(upload.closest('.salesTabs'));
    expect(upload.compareDocumentPosition(add) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const filled = [...document.querySelectorAll<HTMLElement>('.mantine-Button-root')].filter(
      isFilled,
    );
    expect(filled).toEqual([upload]);
  });

  it('на вкладке лидов кнопки нет: гипотезу заводят там, где гипотезы', async () => {
    await open(
      '/sales?tab=leads&hypothesis=1',
      {
        ...remembering([EN], card(2, 'x')),
        'GET /api/sales/leads?hypothesis=1': {
          body: {
            rows: [],
            total: 0,
            page: 1,
            limit: 20,
            states: { new: 0, ready: 0, rejected: 0 },
            reasons: {},
          },
        },
      },
      'Лидов пока нет.',
    );

    expect(screen.queryByRole('button', { name: 'Новая гипотеза' })).not.toBeInTheDocument();
  });

  it('пустое имя — словами у поля, на сервер ничего не уходит', async () => {
    const { recorded, user } = await open(
      '/sales?tab=hypotheses',
      remembering([EN], card(2, 'x')),
      'сайты EN',
    );

    const dialog = await openDialog(user);
    // Пустая новая форма не краснеет сразу.
    expect(within(dialog).queryByText(NO_NAME)).not.toBeInTheDocument();
    await user.type(within(dialog).getByRole('textbox', { name: /Имя/ }), '   ');
    await user.click(within(dialog).getByRole('button', { name: 'Завести' }));

    expect(within(dialog).getByText(NO_NAME)).toBeInTheDocument();
    expect(adds(recorded)).toEqual([]);
  });

  it('отказ сервера — словами над полями, набранное на месте, окно открыто', async () => {
    const taken = 'имя «сайты EN» уже у гипотезы №1';
    const { user } = await open(
      '/sales?tab=hypotheses',
      {
        'GET /api/sales/hypotheses': { body: { rows: [EN], total: 1 } },
        [`POST ${ADD}`]: { status: 409, body: { detail: taken } },
      },
      'сайты EN',
    );

    const dialog = await openDialog(user);
    const name = within(dialog).getByRole('textbox', { name: /Имя/ });
    await user.type(name, 'сайты EN');
    await user.click(within(dialog).getByRole('button', { name: 'Завести' }));

    const alert = (
      await within(dialog).findByText('Гипотеза не заведена', {}, SCREEN_WAIT)
    ).closest('[role="alert"]');
    expect(alert).toHaveTextContent(taken);
    expect(name).toHaveValue('сайты EN');
  });

  it('заведённая — в таблице, окно закрыто, на сервер ушли имя и описание', async () => {
    const made = card(2, 'сайты DE', 'сайты о путешествиях');
    const { recorded, user } = await open(
      '/sales?tab=hypotheses',
      remembering([EN], made),
      'сайты EN',
    );

    const dialog = await openDialog(user);
    await user.type(within(dialog).getByRole('textbox', { name: /Имя/ }), 'сайты DE');
    await user.type(
      within(dialog).getByRole('textbox', { name: /Описание/ }),
      'сайты о путешествиях',
    );
    await user.click(within(dialog).getByRole('button', { name: 'Завести' }));

    expect(await screen.findByText('сайты о путешествиях', {}, SCREEN_WAIT)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), SCREEN_WAIT);
    expect(adds(recorded).map((call) => call.body)).toEqual([
      { name: 'сайты DE', description: 'сайты о путешествиях' },
    ]);
    expect(
      await screen.findByText('Гипотеза «сайты DE» заведена', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
  });
});

describe('новая гипотеза: мастер загрузки', () => {
  it('гипотез нет — плашка зовёт к окну, а не к команде; заведённая сразу выбрана', async () => {
    const { recorded, user } = await open(
      '/sales/import',
      remembering([], card(7, 'сайты DE')),
      'Гипотез пока нет',
    );

    expect(screen.getByText(/Заведите её здесь — кнопкой «Новая/)).toBeInTheDocument();
    expect(screen.getByText('Заведите гипотезу — куда лягут лиды.')).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/outreach /);
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByRole('textbox', { name: /Имя/ }), 'сайты DE');
    await user.click(within(dialog).getByRole('button', { name: 'Завести' }));

    const field = await screen.findByRole('textbox', { name: 'Гипотеза' }, SCREEN_WAIT);
    await waitFor(() => expect(field).toHaveValue('сайты DE'), SCREEN_WAIT);
    expect(screen.queryByText(/Гипотез пока нет/)).not.toBeInTheDocument();
    expect(screen.getByText('Выберите файл.')).toBeInTheDocument();
    // Описание не вписано — уходит «не задано», а не пустой строкой.
    expect(adds(recorded).map((call) => call.body)).toEqual([
      { name: 'сайты DE', description: null },
    ]);
  });

  it('гипотезы есть — заведённая встаёт в список и выбрана вместо пустого выбора', async () => {
    const { user } = await open(
      '/sales/import',
      remembering([EN], card(7, 'сайты DE')),
      'Выберите гипотезу — куда лягут лиды.',
    );

    const field = screen.getByRole('textbox', { name: 'Гипотеза' });
    expect(field).toHaveValue('');
    const dialog = await openDialog(user);
    await user.type(within(dialog).getByRole('textbox', { name: /Имя/ }), 'сайты DE');
    await user.click(within(dialog).getByRole('button', { name: 'Завести' }));

    await waitFor(() => expect(field).toHaveValue('сайты DE'), SCREEN_WAIT);
    expect(screen.queryByText('Выберите гипотезу — куда лягут лиды.')).not.toBeInTheDocument();
  });
});
