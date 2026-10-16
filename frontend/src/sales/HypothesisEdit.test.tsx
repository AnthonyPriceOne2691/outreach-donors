/**
 * Правка гипотезы на вкладке «Гипотезы» — «Править» под именем открывает то же окно, что
 * «Новая гипотеза», в режиме правки (волна 15). Ответы сервера — записанные (`serve()`); сервер
 * помнит правку: список после неё — уже с новым именем. Имена выдуманы, разных письменностей.
 *
 * Проверяется то, ради чего правка: на сервер уходит **только поправленное** (не тронутое
 * описание не затирает правку «Сбора базы»); ничего не поправили — запроса нет; пустое имя —
 * словами у поля и без запроса; отказ сервера — словами над полями, набранное на месте; что
 * зависит от имени и описания — в «i» у подписей. Описание в строке — три строки и
 * «показать целиком».
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { HypothesisCard } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';

const SCREEN_WAIT = { timeout: 5000 };
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

const KB = {
  rows: [],
  total: 0,
  active: 0,
  version: 'kb-000000000000',
  kinds: ['brief'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

type Route = Answer | ((call: Call) => Answer);

/** Сервер, который помнит правку: `PATCH` меняет гипотезу в списке. */
function remembering(start: HypothesisCard[]): Record<string, Route> {
  const known = start.map((row) => ({ ...row }));
  const routes: Record<string, Route> = {
    'GET /api/sales/hypotheses': () => ({ body: { rows: [...known], total: known.length } }),
  };
  for (const row of known) {
    routes[`GET /api/sales/hypotheses/${row.id}/holds`] = {
      body: { hypothesis_id: row.id, holds: {}, held: null },
    };
    routes[`PATCH /api/sales/hypotheses/${row.id}`] = (call) => {
      const body = call.body as { name?: string; description?: string };
      if (body.name !== undefined) row.name = body.name.split(/\s+/).filter(Boolean).join(' ');
      if (body.description !== undefined) row.description = body.description.trim() || null;
      return {
        body: { hypothesis_id: row.id, name: row.name, description: row.description },
      };
    };
  }
  return routes;
}

async function open(routes: Record<string, Route>) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/kb': { body: KB },
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales?tab=hypotheses');
  // Строк несколько — ждём первую по имени: у «Найти» с регуляркой совпали бы все.
  await screen.findByRole('button', { name: 'Править гипотезу «сайты EN»' }, SCREEN_WAIT);
  return { recorded, user: userEvent.setup() };
}

function patches(recorded: Recorded): Call[] {
  return recorded.calls.filter((call) => call.method === 'PATCH');
}

async function openEdit(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole('button', { name: `Править гипотезу «${name}»` }));
  return screen.findByRole('dialog', { name: `Гипотеза «${name}»` }, SCREEN_WAIT);
}

const EN = card(1, 'сайты EN', 'Блоги о путешествиях: пишут сами, берут рекламу.');
const JP = card(2, '物流 日本');

describe('правка гипотезы: окно в режиме правки', () => {
  it('окно — то же, что заведение: поля заполнены, «Сохранить», «i» — что зависит от полей', async () => {
    const { user } = await open(remembering([EN, JP]));

    const dialog = await openEdit(user, 'сайты EN');

    expect(within(dialog).getByRole('textbox', { name: /Имя/ })).toHaveValue('сайты EN');
    expect(within(dialog).getByRole('textbox', { name: /Описание/ })).toHaveValue(EN.description);
    expect(within(dialog).getByRole('button', { name: 'Сохранить' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Что значит «Имя»' })).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: 'Что значит «Описание»' }),
    ).toBeInTheDocument();
  });

  it('поправили одно имя — на сервер уходит только имя; список — с новым, окно закрыто', async () => {
    const { recorded, user } = await open(remembering([EN, JP]));

    const dialog = await openEdit(user, 'сайты EN');
    const name = within(dialog).getByRole('textbox', { name: /Имя/ });
    await user.clear(name);
    await user.type(name, 'блоги EN');
    await user.click(within(dialog).getByRole('button', { name: 'Сохранить' }));

    expect(
      await screen.findByRole('button', { name: 'Править гипотезу «блоги EN»' }, SCREEN_WAIT),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), SCREEN_WAIT);
    expect(patches(recorded).map((call) => [call.path, call.body])).toEqual([
      ['/api/sales/hypotheses/1', { name: 'блоги EN' }],
    ]);
    expect(
      await screen.findByText('Гипотеза «блоги EN» сохранена', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
  });

  it('поправили одно описание — уходит только описание: имя не трогается', async () => {
    const { recorded, user } = await open(remembering([EN, JP]));

    const dialog = await openEdit(user, '物流 日本');
    await user.type(
      within(dialog).getByRole('textbox', { name: /Описание/ }),
      'Логистические компании Японии.',
    );
    await user.click(within(dialog).getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(patches(recorded)).toHaveLength(1), SCREEN_WAIT);
    expect(patches(recorded)[0]?.body).toEqual({ description: 'Логистические компании Японии.' });
  });

  it('ничего не поправили (пробелы в имени не в счёт) — окно закрыто, запроса нет', async () => {
    const { recorded, user } = await open(remembering([EN, JP]));

    const dialog = await openEdit(user, 'сайты EN');
    const name = within(dialog).getByRole('textbox', { name: /Имя/ });
    await user.clear(name);
    await user.type(name, '  сайты   EN ');
    await user.click(within(dialog).getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument(), SCREEN_WAIT);
    expect(patches(recorded)).toEqual([]);
  });

  it('пустое имя — словами у поля, на сервер ничего не уходит', async () => {
    const { recorded, user } = await open(remembering([EN, JP]));

    const dialog = await openEdit(user, 'сайты EN');
    await user.clear(within(dialog).getByRole('textbox', { name: /Имя/ }));
    await user.click(within(dialog).getByRole('button', { name: 'Сохранить' }));

    expect(within(dialog).getByText(NO_NAME)).toBeInTheDocument();
    expect(patches(recorded)).toEqual([]);
  });

  it('отказ сервера — словами над полями, набранное на месте, окно открыто', async () => {
    const taken =
      'рассылка «Продажи: блоги EN» уже есть (№7, писем — 3) — письма двух гипотез легли бы в одну рассылку: выберите другое имя';
    const { user } = await open({
      ...remembering([EN, JP]),
      'PATCH /api/sales/hypotheses/1': { status: 409, body: { detail: taken } },
    });

    const dialog = await openEdit(user, 'сайты EN');
    const name = within(dialog).getByRole('textbox', { name: /Имя/ });
    await user.clear(name);
    await user.type(name, 'блоги EN');
    await user.click(within(dialog).getByRole('button', { name: 'Сохранить' }));

    const alert = (
      await within(dialog).findByText('Гипотеза не сохранена', {}, SCREEN_WAIT)
    ).closest('[role="alert"]');
    expect(alert).toHaveTextContent(taken);
    expect(name).toHaveValue('блоги EN');
  });
});

describe('описание в строке гипотезы: три строки и «показать целиком»', () => {
  // Замер — своими свойствами прототипа поверх унаследованных от `Element`: снять их —
  // вернуть jsdom его нули.
  afterEach(() => {
    Reflect.deleteProperty(HTMLElement.prototype, 'scrollHeight');
    Reflect.deleteProperty(HTMLElement.prototype, 'clientHeight');
  });

  /** Описание длиннее трёх строк — так его меряет браузер; в jsdom раскладки нет. */
  function longerThanThreeLines() {
    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
      configurable: true,
      get(this: HTMLElement) {
        return this.classList.contains('hypothesisDescription') ? 120 : 0;
      },
    });
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
      configurable: true,
      get(this: HTMLElement) {
        return this.classList.contains('hypothesisDescription') ? 54 : 0;
      },
    });
  }

  it('длинное — свёрнуто до трёх строк, раскрывается и сворачивается обратно', async () => {
    longerThanThreeLines();
    const { user } = await open(remembering([EN, JP]));

    const text = screen.getByText(EN.description ?? '');
    expect(text).toHaveAttribute('data-line-clamp', 'true');
    await user.click(screen.getByRole('button', { name: 'показать целиком' }));
    expect(text).not.toHaveAttribute('data-line-clamp');
    await user.click(screen.getByRole('button', { name: 'свернуть' }));
    expect(text).toHaveAttribute('data-line-clamp', 'true');
  });

  it('короткое — целиком, без ссылки раскрытия', async () => {
    await open(remembering([EN, JP]));

    expect(screen.getByText(EN.description ?? '')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'показать целиком' })).not.toBeInTheDocument();
  });
});
