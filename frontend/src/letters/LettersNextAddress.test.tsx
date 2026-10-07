/**
 * Экран писем после отказов доставки: пустая очередь говорит, кому письмо
 * уйдёт на следующий адрес и у кого адреса кончились.
 *
 * Сервер шлёт эти строки воронки только ненулевыми (`letters/funnel.py`).
 * Без них у донора, которому все письма не дошли, пустая очередь говорила
 * бы «написаны все» — а ему нужен новый адрес, а не ожидание ответа.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, NO_STUCK_LETTERS, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const VIEW = {
  letters: [],
  followup_default: [7, 14],
  letter_default: {
    subject: 'Guest article on {{host}}',
    zones: [
      { name: 'greeting', kind: 'rewrite', title: 'Приветствие', text: 'Hi,' },
      { name: 'signature', kind: 'fixed', title: 'Подпись', text: 'Best regards' },
    ],
  },
  blocked_by: [],
  transport: { name: 'null', real: false, problem: null },
  corridor: { min: 0.15, max: 0.25 },
};

async function openLetters(routes: Record<string, unknown>): Promise<void> {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  serve({
    'GET /api/auth/me': { body: ADMIN },
    'GET /api/runs/with-accepted': { body: [] },
    ...NO_STUCK_LETTERS,
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/letters');
  await screen.findByText('Очередь пуста');
}

describe('экран писем: следующий адрес', () => {
  it('донорам — сколько на следующий адрес и у скольких адреса кончились', async () => {
    await openLetters({
      'GET /api/letters': {
        body: {
          ...VIEW,
          funnel: {
            подходящих: 12,
            'с адресом': 9,
            'вне стоп-листа': 9,
            'ещё не писали': 3,
            'из них на следующий адрес': 2,
            'адреса кончились': 1,
          },
        },
      },
    });

    expect(
      screen.getByText(/Из них на следующий адрес — 2: прежнее письмо не дошло\./),
    ).toBeVisible();
    expect(
      screen.getByText(
        /У 1 донора адреса кончились: прежние письма не дошли — новый адрес вписывают в карточке донора\./,
      ),
    ).toBeVisible();
  });

  it('без отказов — ни слова о них', async () => {
    await openLetters({
      'GET /api/letters': {
        body: {
          ...VIEW,
          funnel: { подходящих: 12, 'с адресом': 9, 'вне стоп-листа': 9, 'ещё не писали': 0 },
        },
      },
    });

    expect(screen.queryByText(/следующий адрес/)).toBeNull();
    expect(screen.queryByText(/адреса кончились/)).toBeNull();
  });

  it('рекламодателям — число словом «рекламодателей» и без совета про карточку', async () => {
    await openLetters({
      'GET /api/letters': { body: { ...VIEW, funnel: { подходящих: 0, 'ещё не писали': 0 } } },
      'GET /api/letters?stage=advertisers': {
        body: {
          ...VIEW,
          stage: 'advertisers',
          funnel: {
            рекламодателей: 5,
            'со ссылкой': 5,
            'цена донора свежая': 5,
            'с адресом': 5,
            'вне стоп-листа': 5,
            'ещё не писали': 0,
            'адреса кончились': 5,
          },
        },
      },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('radio', { name: 'Рекламодателям' }));

    expect(
      await screen.findByText(/У 5 рекламодателей адреса кончились: прежние письма не дошли\./),
    ).toBeVisible();
    expect(screen.queryByText(/в карточке донора/)).toBeNull();
  });
});
