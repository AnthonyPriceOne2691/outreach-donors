/**
 * Смена сотрудника в одной вкладке.
 *
 * Найдено живым прогоном 19.09.2026: после выхода и входа другим человеком
 * экран показывал предыдущего — его почту, роль и меню, — хотя запросы уже
 * уходили с новым пропуском. То есть оператор видел раздел, в который
 * сервер его не пустит.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, OPERATOR, signedIn } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

async function enter(email: string, password: string) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText('Почта'), email);
  await user.type(screen.getByLabelText('Пароль'), password);
  await user.click(screen.getByRole('button', { name: 'Войти' }));
}

describe('смена сотрудника в одной вкладке', () => {
  it('второй вход показывает второго, а не первого', async () => {
    serve({
      'POST /api/auth/login': (call) => ({
        body: signedIn((call.body as { email: string }).email === ADMIN.email ? ADMIN : OPERATOR),
      }),
      'GET /api/auth/me': { body: OPERATOR },
      'GET /api/users': { body: [] },
    });
    renderWith(<AppRoutes />, '/login');

    await enter(ADMIN.email, 'пароль-для-теста');
    expect(await screen.findByText('Учётки')).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole('button', { name: 'Выйти' }));
    await enter(OPERATOR.email, 'пароль-для-теста');

    // Почта встречается и в шапке, и в обзоре — проверяем обе разом.
    await waitFor(() => {
      expect(screen.getAllByText(OPERATOR.email).length).toBeGreaterThan(0);
    });
    expect(screen.queryAllByText(ADMIN.email)).toHaveLength(0);
    // И меню — тоже второго: пункт, ведущий в отказ, хуже отсутствующего.
    expect(screen.queryByText('Учётки')).not.toBeInTheDocument();
  });
});

describe('пропуск отозвали на стороне сервера', () => {
  it('отказ на любом запросе роняет сессию, а не только тот экран', async () => {
    serve({
      'POST /api/auth/login': { body: signedIn(ADMIN) },
      'GET /api/users': { status: 401, body: { detail: 'Пропуск просрочен — войдите заново' } },
    });
    renderWith(<AppRoutes />, '/login');

    await enter(ADMIN.email, 'пароль-для-теста');
    const user = userEvent.setup();
    await user.click(await screen.findByText('Учётки'));

    // Пропуск выброшен клиентом — интерфейс не должен оставаться
    // нарисованным для человека, которого уже не пускают.
    expect(await screen.findByText('Вход для сотрудников')).toBeInTheDocument();
  });
});
