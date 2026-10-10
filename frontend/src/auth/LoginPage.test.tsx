/**
 * Вход глазами человека: что он вводит и что видит в ответ.
 *
 * Проверяется не «сервер умеет», а «показано и отправлено» — сервер
 * заменён записанными ответами.
 */

import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, HOME_ROUTES, NEWCOMER, TOKEN_KEY, signedIn } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

async function enter(email: string, password: string) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('Почта'), email);
  await user.type(screen.getByLabelText('Пароль'), password);
  await user.click(screen.getByRole('button', { name: 'Войти' }));
}

describe('вход', () => {
  it('пускает и запоминает пропуск', async () => {
    const recorded = serve({ 'POST /api/auth/login': { body: signedIn(ADMIN) }, ...HOME_ROUTES });
    renderWith(<AppRoutes />, '/login');

    await enter('админ@site.com', 'пароль-для-теста');

    expect(await screen.findByText(/Вошли как/)).toBeInTheDocument();
    expect(localStorage.getItem(TOKEN_KEY)).toBe(signedIn(ADMIN).token);
    expect(recorded.calls[0]?.body).toEqual({
      email: 'админ@site.com',
      password: 'пароль-для-теста',
    });
  });

  it('показывает отказ сервера тем же текстом', async () => {
    serve({
      'POST /api/auth/login': { status: 401, body: { detail: 'Неверная почта или пароль' } },
    });
    renderWith(<AppRoutes />, '/login');

    await enter('админ@site.com', 'мимо');

    expect(await screen.findByText('Неверная почта или пароль')).toBeInTheDocument();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it('перебор — это «подождите», а не «неверный пароль»', async () => {
    serve({
      'POST /api/auth/login': {
        status: 429,
        body: { detail: 'Слишком много попыток входа. Повторите через 59 с.' },
        headers: { 'Retry-After': '59' },
      },
    });
    renderWith(<AppRoutes />, '/login');

    await enter('админ@site.com', 'мимо');

    expect(await screen.findByText('Подождите')).toBeInTheDocument();
    expect(screen.getByText(/Повторите через 59/)).toBeInTheDocument();
  });

  it('неверный пароль — отказ входа, а не «сеанс закончился»: сеанса и не было', async () => {
    serve({
      'POST /api/auth/login': { status: 401, body: { detail: 'Неверная почта или пароль' } },
    });
    renderWith(<AppRoutes />, '/login');

    await enter('админ@site.com', 'мимо');

    expect(await screen.findByText('Неверная почта или пароль')).toBeInTheDocument();
    expect(screen.queryByText('Сеанс закончился')).not.toBeInTheDocument();
  });

  it('с разовым паролем ведёт сразу на смену, а не на обзор', async () => {
    serve({ 'POST /api/auth/login': { body: signedIn(NEWCOMER) } });
    renderWith(<AppRoutes />, '/login');

    await enter('новичок@site.com', 'разовый');

    await waitFor(() => {
      expect(screen.getByText('Смена пароля')).toBeInTheDocument();
    });
    expect(screen.getByText(/Пока он не сменён, остальное закрыто/)).toBeInTheDocument();
  });
});

describe('вход после оборванного сеанса', () => {
  it('учётку отключили посреди работы — вход говорит, почему пришлось войти снова', async () => {
    // Проверка QA 10.10.2026: следующий запрос получал 401, и человек молча
    // оказывался на входе.
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/suppressions': {
        status: 401,
        body: { detail: 'Учётка отключена или удалена — войти по этому пропуску нельзя' },
      },
    });
    renderWith(<AppRoutes />, '/suppressions');

    expect(await screen.findByText('Сеанс закончился')).toBeInTheDocument();
    expect(screen.getByText(/учётку могли отключить/)).toBeInTheDocument();
    expect(screen.getByText('Вход для сотрудников')).toBeInTheDocument();
  });

  it('выход кнопкой — без объяснений: человек вышел сам', async () => {
    serve({ 'POST /api/auth/login': { body: signedIn(ADMIN) }, ...HOME_ROUTES });
    renderWith(<AppRoutes />, '/login');
    await enter('админ@site.com', 'пароль-для-теста');

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Выйти' }));

    expect(await screen.findByText('Вход для сотрудников')).toBeInTheDocument();
    expect(screen.queryByText('Сеанс закончился')).not.toBeInTheDocument();
  });
});
