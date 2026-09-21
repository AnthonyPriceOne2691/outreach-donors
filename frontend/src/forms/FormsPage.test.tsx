/**
 * Ручная очередь форм.
 *
 * Ступень, которую нельзя пройти кодом: у донора есть форма и нет
 * почты. До этого экрана очередь существовала значком в общей таблице
 * и больше нигде.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { Call } from '../test/server';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

const QUEUE = {
  rows: [
    {
      donor_id: 7,
      domain_id: 70,
      host: 'form.example.test',
      dr: 55,
      org_traffic: 9000,
      attempted_at: '2026-09-20T10:00:00+00:00',
    },
  ],
  total: 1,
  monthly_left: 98,
  monthly_cap: 100,
};

async function openForms(routes: Record<string, unknown> = {}, who: unknown = ADMIN) {
  localStorage.setItem(TOKEN_KEY, 'пропуск');
  const recorded = serve({
    'GET /api/auth/me': { body: who },
    'GET /api/contacts/forms': { body: QUEUE },
    ...(routes as Record<string, never>),
  });
  renderWith(<AppRoutes />, '/forms');
  await screen.findByText('form.example.test');
  return recorded;
}

describe('ручная очередь форм', () => {
  it('остаток месячного потолка виден числом', async () => {
    await openForms();

    // Человек, разбирающий пачку, должен видеть, сколько ещё можно
    // взять: без потолка очередь никто никогда не разберёт.
    expect(screen.getByText(/98/)).toBeInTheDocument();
  });

  it('вписанный адрес уходит на сервер', async () => {
    const recorded = await openForms({
      'POST /api/contacts/forms/7/filled': { body: { ...QUEUE.rows[0] } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Вписать адрес' }));
    await user.type(await screen.findByLabelText('Адрес почты'), 'editor@form.example.test');
    await user.click(screen.getByRole('button', { name: 'Записать' }));

    await screen.findByText(/адрес записан/);
    const sent = recorded.calls.filter((call: Call) => call.method === 'POST');
    expect(sent[0]?.body).toEqual({ email: 'editor@form.example.test' });
  });

  it('без адреса кнопка записи заперта', async () => {
    await openForms();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Вписать адрес' }));

    expect(await screen.findByRole('button', { name: 'Записать' })).toBeDisabled();
  });

  it('«не вышло» закрывает строку', async () => {
    const recorded = await openForms({
      'POST /api/contacts/forms/7/give-up': { body: { ...QUEUE.rows[0] } },
    });
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Не вышло' }));

    await screen.findByText(/закрыт без адреса/);
    expect(recorded.calls.some((call: Call) => call.path.endsWith('/give-up'))).toBe(true);
  });

  it('оператор без права видит очередь, но не правит её', async () => {
    await openForms({}, { ...(OPERATOR as object), permissions: ['view'] });

    expect(screen.getByText('form.example.test')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Вписать адрес' })).not.toBeInTheDocument();
  });
});
