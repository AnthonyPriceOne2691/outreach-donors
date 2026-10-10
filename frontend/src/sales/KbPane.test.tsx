/**
 * Вкладка «База знаний»: список с версией, включение переключателем, окно записи,
 * «что увидит агент». Тексты выдуманы; ответы сервера — записанные (`serve()`),
 * незаписанный запрос роняет тест.
 *
 * Проверяется то, ради чего вкладка: что уходит на сервер (тело правки,
 * `active`), что экран перечитывает после правки, и что отказ сервера виден
 * словами, а не пропадает. Без права отправки писем записи и «что увидит агент»
 * видны, а записать нечем — сказано строкой.
 */

import { notifications } from '@mantine/notifications';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AppRoutes } from '../App';
import type { AgentView, HypothesesView, KbEntryCard, KbView } from '../api/salesTypes';
import type { Me } from '../api/types';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';

const SCREEN_WAIT = { timeout: 5000 };

const PRICE: KbEntryCard = {
  id: 7,
  kind: 'price_policy',
  language: 'ru',
  title: 'Цена аудита',
  text: 'Цену называем после короткого созвона.',
  tags: ['аудит', 'цена'],
  active: true,
  updated_by: 'seller@ours.example.test',
  created_at: '2026-10-05T09:00:00+00:00',
  updated_at: '2026-10-05T10:00:00+00:00',
};

const OLD_CASE: KbEntryCard = {
  ...PRICE,
  id: 8,
  kind: 'case',
  language: 'en',
  title: 'Made-up shop',
  text: 'Doubled made-up traffic.',
  tags: [],
  active: false,
  updated_by: null,
};

const KB: KbView = {
  rows: [PRICE, OLD_CASE],
  total: 2,
  active: 1,
  version: 'kb-3f2a9c1d0b7e',
  kinds: ['brief', 'service', 'case', 'objection', 'price_policy', 'forbidden', 'cta'],
  limits: { title: 255, text: 20000, tag: 64, tags: 20 },
};

const HYPOTHESES: HypothesesView = { rows: [], total: 0, module_enabled: true };

async function openKb(
  routes: Record<string, Answer | ((call: Call) => Answer)> = {},
  who: Me = ADMIN,
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/sales/hypotheses': { body: HYPOTHESES },
    'GET /api/sales/kb': { body: KB },
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales?tab=kb');
  await screen.findByText('Цена аудита', {}, SCREEN_WAIT);
  return recorded;
}

function rowOf(title: string): HTMLElement {
  const row = screen.getByText(title).closest('tr');
  if (row === null) throw new Error(`строки ${title} нет`);
  return row;
}

function calls(recorded: Recorded, method: string, path: string): Call[] {
  return recorded.calls.filter((call) => call.method === method && call.path === path);
}

describe('база знаний: список', () => {
  it('колонка правки — «Кто и когда»: в ней и автор, и время (аудит 09.10.2026)', async () => {
    await openKb();

    expect(screen.getByRole('columnheader', { name: 'Кто и когда' })).toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Правил' })).toBeNull();
  });

  it('вступление — одной строкой, что с выключенными записями — в «i» (аудит 09.10.2026)', async () => {
    await openKb();
    const user = userEvent.setup();

    expect(screen.getByText('Агент пишет только из включённых записей.')).toBeInTheDocument();
    expect(screen.queryByText(/Выключенная остаётся в списке/)).toBeNull();

    await user.hover(screen.getByRole('button', { name: 'Что агент видит из базы' }));

    expect(
      await screen.findByText(/Выключенная остаётся в списке/, {}, SCREEN_WAIT),
    ).toBeInTheDocument();
  });

  it('строка называет запись, вид, язык, теги и кто правил; над таблицей — версия', async () => {
    await openKb();

    const price = within(rowOf('Цена аудита'));
    expect(price.getByText('Цену называем после короткого созвона.')).toBeInTheDocument();
    expect(price.getByText('цены')).toBeInTheDocument();
    expect(price.getByText('ru')).toBeInTheDocument();
    expect(price.getByText('аудит, цена')).toBeInTheDocument();
    expect(price.getByText('seller@ours.example.test')).toBeInTheDocument();
    expect(price.getByRole('switch', { name: 'Агент видит «Цена аудита»' })).toBeChecked();

    const old = within(rowOf('Made-up shop'));
    expect(old.getByText('кейс')).toBeInTheDocument();
    expect(old.getByRole('switch', { name: 'Агент видит «Made-up shop»' })).not.toBeChecked();

    expect(screen.getByText('kb-3f2a9c1d0b7e')).toBeInTheDocument();
    expect(screen.getByText(/агент видит 1 из 2/)).toBeInTheDocument();
    expect(screen.getByText('База знаний — 2')).toBeInTheDocument();
  });

  it('записей нет — сказано, что агенту не из чего писать и как наполнить', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/sales/hypotheses': { body: HYPOTHESES },
      'GET /api/sales/kb': { body: { ...KB, rows: [], total: 0, active: 0 } },
    });
    renderWith(<AppRoutes />, '/sales?tab=kb');

    expect(
      await screen.findByText('Записей пока нет — агенту не из чего писать.', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Заведите запись здесь; базу целиком из файла загружает администратор/),
    ).toBeInTheDocument();
  });

  it('база не прочиталась — отказ сервера словами', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/sales/hypotheses': { body: HYPOTHESES },
      'GET /api/sales/kb': { status: 503, body: { detail: 'База не ответила — повторите' } },
    });
    renderWith(<AppRoutes />, '/sales?tab=kb');

    expect(
      await screen.findByText('База знаний не загрузилась', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(screen.getByText('База не ответила — повторите')).toBeInTheDocument();
  });
});

describe('база знаний: включение', () => {
  it('переключатель выключает запись правкой active, и список перечитывается', async () => {
    const recorded = await openKb({
      'PATCH /api/sales/kb/7': { body: { ...PRICE, active: false } },
    });
    const before = calls(recorded, 'GET', '/api/sales/kb').length;

    await userEvent.click(screen.getByRole('switch', { name: 'Агент видит «Цена аудита»' }));

    await waitFor(() => expect(calls(recorded, 'PATCH', '/api/sales/kb/7')).toHaveLength(1));
    expect(calls(recorded, 'PATCH', '/api/sales/kb/7')[0]?.body).toEqual({ active: false });
    await waitFor(() =>
      expect(calls(recorded, 'GET', '/api/sales/kb').length).toBeGreaterThan(before),
    );
  });

  it('отказ переключения — уведомлением словами сервера, само не исчезает', async () => {
    await openKb({
      'PATCH /api/sales/kb/7': {
        status: 404,
        body: { detail: 'записи базы знаний №7 нет — обновите список' },
      },
    });
    const show = vi.spyOn(notifications, 'show');

    await userEvent.click(screen.getByRole('switch', { name: 'Агент видит «Цена аудита»' }));

    expect(
      await screen.findByText('записи базы знаний №7 нет — обновите список', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    expect(screen.getByText('Не переключили')).toBeInTheDocument();
    // Отказ закрывает человек (`notices.ts`): ушёл к соседнему окну — увидит, вернувшись.
    expect(show).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Не переключили', autoClose: false }),
    );
  });
});

describe('база знаний: окно записи', () => {
  it('новая запись: пустая форма не краснеет, «Завести» ждёт полей, тело уходит целиком', async () => {
    const recorded = await openKb({
      'POST /api/sales/kb': (call) => ({
        status: 201,
        body: { ...PRICE, id: 9, ...(call.body as object) },
      }),
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Добавить запись' }));
    const dialog = within(await screen.findByRole('dialog', {}, SCREEN_WAIT));
    expect(dialog.queryByText(/Впишите/)).not.toBeInTheDocument();
    expect(dialog.getByRole('button', { name: 'Завести' })).toBeDisabled();

    await user.type(dialog.getByRole('textbox', { name: 'Язык' }), 'ru');
    await user.type(dialog.getByRole('textbox', { name: 'Заголовок' }), 'Кто мы');
    await user.type(dialog.getByRole('textbox', { name: 'Текст' }), 'Студия примеров.');
    await user.type(dialog.getByRole('textbox', { name: 'Теги' }), 'фон{Enter}');
    await user.click(dialog.getByRole('button', { name: 'Завести' }));

    await waitFor(() => expect(calls(recorded, 'POST', '/api/sales/kb')).toHaveLength(1));
    expect(calls(recorded, 'POST', '/api/sales/kb')[0]?.body).toEqual({
      kind: 'brief',
      language: 'ru',
      title: 'Кто мы',
      text: 'Студия примеров.',
      tags: ['фон'],
      active: true,
    });
    expect(
      await screen.findByText('Запись «Кто мы» сохранена', {}, SCREEN_WAIT),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('стёртое поле — «впишите» под ним, длинное — граница сервера до нажатия', async () => {
    // Граница — из ответа сервера: экран своего числа не держит.
    await openKb({ 'GET /api/sales/kb': { body: { ...KB, limits: { ...KB.limits, text: 40 } } } });
    const user = userEvent.setup();

    await user.click(within(rowOf('Цена аудита')).getByRole('button', { name: 'Править' }));
    const dialog = within(await screen.findByRole('dialog', {}, SCREEN_WAIT));
    await user.clear(dialog.getByRole('textbox', { name: 'Заголовок' }));
    await user.type(dialog.getByRole('textbox', { name: 'Текст' }), ' и ещё');

    expect(dialog.getByText('Впишите заголовок — по нему запись узнают')).toBeInTheDocument();
    expect(dialog.getByText('Длиннее 40 знаков: сейчас 44')).toBeInTheDocument();
    expect(dialog.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
  });

  it('правка уходит телом записи; отказ сервера — словами над формой, набранное стоит', async () => {
    const recorded = await openKb({
      'PATCH /api/sales/kb/7': {
        status: 409,
        body: { detail: 'запись «Кто мы» (price_policy, ru) уже есть — №3: правьте её' },
      },
    });
    const user = userEvent.setup();

    await user.click(within(rowOf('Цена аудита')).getByRole('button', { name: 'Править' }));
    const dialog = within(await screen.findByRole('dialog', {}, SCREEN_WAIT));
    const title = dialog.getByRole('textbox', { name: 'Заголовок' });
    await user.clear(title);
    await user.type(title, 'Кто мы');
    await user.click(dialog.getByRole('button', { name: 'Сохранить' }));

    expect(
      await dialog.findByText(
        'запись «Кто мы» (price_policy, ru) уже есть — №3: правьте её',
        {},
        SCREEN_WAIT,
      ),
    ).toBeInTheDocument();
    expect(calls(recorded, 'PATCH', '/api/sales/kb/7')[0]?.body).toEqual({
      kind: 'price_policy',
      language: 'ru',
      title: 'Кто мы',
      text: PRICE.text,
      tags: PRICE.tags,
      active: true,
    });
    expect(title).toHaveValue('Кто мы');
  });
});

describe('база знаний: что увидит агент', () => {
  const SEEN: AgentView = {
    version: 'kb-3f2a9c1d0b7e',
    total: 1,
    groups: [
      {
        kind: 'price_policy',
        language: 'ru',
        facts: [
          { id: 7, title: 'Цена аудита', text: 'Цену называем\nпосле созвона.', tags: ['цена'] },
        ],
      },
    ],
  };

  it('окно спрашивает выборку агента и показывает её группами по виду и языку', async () => {
    const recorded = await openKb({ 'GET /api/sales/kb/preview': { body: SEEN } });

    await userEvent.click(screen.getByRole('button', { name: 'Что увидит агент' }));

    const dialog = within(await screen.findByRole('dialog', {}, SCREEN_WAIT));
    expect(await dialog.findByText('цены · ru', {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(dialog.getByText('Цена аудита')).toBeInTheDocument();
    expect(dialog.getByText('теги: цена')).toBeInTheDocument();
    expect(dialog.getByText(/Выключенных здесь нет/)).toBeInTheDocument();
    // Выключенной записи в окне нет: её нет в ответе — экран своего фильтра не держит.
    expect(dialog.queryByText('Made-up shop')).not.toBeInTheDocument();
    expect(calls(recorded, 'GET', '/api/sales/kb/preview')).toHaveLength(1);
  });

  it('включённых нет — сказано, что агенту не из чего писать', async () => {
    await openKb({ 'GET /api/sales/kb/preview': { body: { ...SEEN, total: 0, groups: [] } } });

    await userEvent.click(screen.getByRole('button', { name: 'Что увидит агент' }));

    expect(
      await screen.findByText(/Агенту не из чего писать: включённых записей нет/, {}, SCREEN_WAIT),
    ).toBeInTheDocument();
  });
});

describe('база знаний: без права на отправку', () => {
  const LINE = 'Правит базу знаний тот, у кого есть право отправки писем. Смотреть можно всем.';
  const SEEN: AgentView = {
    version: 'kb-3f2a9c1d0b7e',
    total: 1,
    groups: [
      {
        kind: 'price_policy',
        language: 'ru',
        facts: [{ id: PRICE.id, title: PRICE.title, text: PRICE.text, tags: PRICE.tags }],
      },
    ],
  };

  it('записи и «что увидит агент» видны; завести и переключить нельзя — строка говорит почему', async () => {
    const recorded = await openKb({ 'GET /api/sales/kb/preview': { body: SEEN } }, OPERATOR);

    expect(screen.getByText(LINE)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Добавить запись' })).toBeDisabled();
    for (const title of ['Цена аудита', 'Made-up shop']) {
      expect(screen.getByRole('switch', { name: `Агент видит «${title}»` })).toBeDisabled();
    }

    await userEvent.click(screen.getByRole('button', { name: 'Что увидит агент' }));

    const dialog = within(await screen.findByRole('dialog', {}, SCREEN_WAIT));
    expect(await dialog.findByText('цены · ru', {}, SCREEN_WAIT)).toBeInTheDocument();
    expect(recorded.calls.filter((call) => call.method !== 'GET')).toEqual([]);
  });

  it('запись открывают смотреть: текст целиком, поля и «Сохранить» закрыты', async () => {
    await openKb({}, OPERATOR);
    const user = userEvent.setup();

    await user.click(within(rowOf('Made-up shop')).getByRole('button', { name: 'Открыть' }));

    const dialog = within(await screen.findByRole('dialog', {}, SCREEN_WAIT));
    expect(dialog.getByRole('textbox', { name: 'Текст' })).toHaveValue(OLD_CASE.text);
    for (const name of ['Вид', 'Язык', 'Заголовок', 'Текст', 'Теги']) {
      expect(dialog.getByRole('textbox', { name })).toBeDisabled();
    }
    expect(dialog.getByRole('switch', { name: 'Агент видит запись' })).toBeDisabled();
    expect(dialog.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
    expect(dialog.getByText(LINE)).toBeInTheDocument();
  });

  it('с правом отправки строки нет', async () => {
    await openKb();

    expect(screen.queryByText(LINE)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Добавить запись' })).toBeEnabled();
  });
});
