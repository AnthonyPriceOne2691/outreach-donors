/**
 * Вкладка «Отправитель»: готовность к отправке словами сервера, форма целиком,
 * границы сервера до нажатия, отказ словами. Адреса и подписи выдуманы.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { SenderView } from '../api/salesTypes';
import { ADMIN, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';
import type { Answer, Call, Recorded } from '../test/server';

const SCREEN_WAIT = { timeout: 5000 };

const LIMITS: SenderView['limits'] = {
  sender_name: 128,
  sender_position: 128,
  signature: 1000,
  website: 255,
  telegram: 255,
  physical_address: 500,
  call_link: 512,
};

const EMPTY: SenderView = {
  sender_name: null,
  sender_position: null,
  signature: null,
  website: null,
  telegram: null,
  physical_address: null,
  call_link: null,
  updated_by: null,
  updated_at: null,
  missing: ['не задан физический адрес', 'не задана подпись', 'не задано имя отправителя'],
  limits: LIMITS,
};

async function openSender(
  routes: Record<string, Answer | ((call: Call) => Answer)> = {},
  sender: SenderView = EMPTY,
): Promise<Recorded> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/sales/hypotheses': { body: { rows: [], total: 0 } },
    'GET /api/sales/kb': {
      body: {
        rows: [],
        total: 0,
        active: 0,
        version: 'kb-000000000000',
        kinds: ['brief'],
        limits: { title: 255, text: 20000, tag: 64, tags: 20 },
      },
    },
    'GET /api/sales/sender': { body: sender },
    ...routes,
  });
  renderWith(<AppRoutes />, '/sales?tab=sender');
  await screen.findByText(/Отправка продаж/, {}, SCREEN_WAIT);
  return recorded;
}

function field(name: string): HTMLElement {
  return screen.getByRole('textbox', { name });
}

describe('отправитель', () => {
  it('пустой: чего не хватает для отправки — словами сервера, сохранять нечего', async () => {
    await openSender();

    const warning = screen.getByText('Отправка продаж не готова').closest('[role="alert"]');
    expect(warning).toHaveTextContent(
      'не задан физический адрес; не задана подпись; не задано имя отправителя — ' +
        'без этого письмо продаж не уходит.',
    );
    expect(field('Физический адрес')).toHaveValue('');
    expect(screen.getByText('Ещё не заполнялся.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
  });

  it('сохраняется целиком: пустое уходит null, готовность — из ответа сервера', async () => {
    const saved: SenderView = {
      ...EMPTY,
      sender_name: 'Ива Тестова',
      signature: 'Ива Тестова',
      physical_address: 'Выдуманная ул., 7',
      updated_by: 'seller@ours.example.test',
      updated_at: '2026-10-05T10:00:00+00:00',
      missing: [],
    };
    const recorded = await openSender({ 'POST /api/sales/sender': { body: saved } });
    const user = userEvent.setup();

    await user.type(field('Имя отправителя'), 'Ива Тестова');
    await user.type(field('Подпись'), 'Ива Тестова');
    await user.type(field('Физический адрес'), 'Выдуманная ул., 7');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    await waitFor(() => expect(recorded.calls.filter((c) => c.method === 'POST')).toHaveLength(1));
    expect(recorded.calls.find((c) => c.method === 'POST')?.body).toEqual({
      sender_name: 'Ива Тестова',
      sender_position: null,
      signature: 'Ива Тестова',
      physical_address: 'Выдуманная ул., 7',
      website: null,
      telegram: null,
      call_link: null,
    });
    const ready = (await screen.findByText('Отправка продаж готова', {}, SCREEN_WAIT)).closest(
      '[role="alert"]',
    );
    // Поля правила называет только сервер: «готова» их не перечисляет.
    expect(ready).toHaveTextContent('Всё, без чего письмо продаж не уходит, задано.');
    expect(screen.getByText(/Правил seller@ours\.example\.test/)).toBeInTheDocument();
    expect(field('Подпись')).toHaveValue('Ива Тестова');
  });

  it('отказ сервера — словами над формой, набранное стоит', async () => {
    await openSender({
      'POST /api/sales/sender': {
        status: 400,
        body: { detail: 'сайт: «studio.example.test» — не ссылка, ждём https://…' },
      },
    });
    const user = userEvent.setup();

    await user.type(field('Сайт'), 'studio.example.test');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    const refusal = await screen.findByText('Не сохранили', {}, SCREEN_WAIT);
    expect(
      within(refusal.closest('[role="alert"]') as HTMLElement).getByText(/не ссылка/),
    ).toBeInTheDocument();
    expect(field('Сайт')).toHaveValue('studio.example.test');
  });

  it('длиннее границы сервера — под полем до нажатия, на сервер не уходит', async () => {
    const recorded = await openSender({}, { ...EMPTY, limits: { ...LIMITS, sender_name: 5 } });
    const user = userEvent.setup();

    await user.type(field('Имя отправителя'), 'Ива Тестова');

    expect(screen.getByText('Длиннее 5 знаков: сейчас 11')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
    expect(recorded.calls.filter((c) => c.method === 'POST')).toEqual([]);
  });
});
