/**
 * Три проверки перед экраном — и порядок между ними.
 *
 * Отдельный набор, потому что ошибка здесь не видна глазом: экран
 * открывается, данные приходят, и только сервер отказывает — то есть
 * человек упирается в отказ после того, как ему показали кнопку.
 */

import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, NEWCOMER, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

describe('доступ к экрану', () => {
  it('без пропуска ведёт на вход', async () => {
    serve({});
    renderWith(<AppRoutes />, '/users');

    expect(await screen.findByText('Вход для сотрудников')).toBeInTheDocument();
  });

  it('с разовым паролем ведёт на смену, куда бы человек ни шёл', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: NEWCOMER } });
    renderWith(<AppRoutes />, '/users');

    expect(await screen.findByText('Смена пароля')).toBeInTheDocument();
  });

  it('без права объясняет, а не выкидывает', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: OPERATOR } });
    renderWith(<AppRoutes />, '/users');

    // Человек пришёл по ссылке от коллеги: молча переадресовать его
    // некуда, он решит, что сервис сломался.
    expect(await screen.findByText('Раздел недоступен')).toBeInTheDocument();
    expect(screen.getByText(/«users»/)).toBeInTheDocument();
  });

  it('с правом пускает', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: ADMIN }, 'GET /api/users': { body: [] } });
    renderWith(<AppRoutes />, '/users');

    expect(await screen.findByRole('button', { name: 'Завести учётку' })).toBeInTheDocument();
  });

  it('просроченный пропуск возвращает на вход, а не показывает пустой экран', async () => {
    localStorage.setItem(TOKEN_KEY, 'протухший');
    serve({
      'GET /api/auth/me': { status: 401, body: { detail: 'Пропуск просрочен — войдите заново' } },
    });
    renderWith(<AppRoutes />, '/');

    expect(await screen.findByText('Вход для сотрудников')).toBeInTheDocument();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});
