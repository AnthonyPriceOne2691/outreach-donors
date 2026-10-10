/**
 * Доступ сменился посреди сеанса — экран узнаёт об этом от первого же отказа.
 *
 * Проверка прода 10.10.2026: право сняли, а пункт меню остался — карточка «кто я»
 * живёт минуту и по отказу не перечитывалась, щелчок вёл в «Действие «sales»
 * недоступно этой учётке». Перечитывает её отказ с признаком сервера
 * (`X-Access-Refusal`), а не текст отказа.
 */

import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import type { Me } from '../api/types';
import { ADMIN, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve, type Call } from '../test/server';

const NO_USERS = 'Действие «заводить учётки» не выдано этой учётке — права выдаёт админ';

/** Тот же админ, у которого право «заводить учётки» уже сняли. */
const WITHOUT_USERS: Me = {
  ...ADMIN,
  permissions: ADMIN.permissions.filter((permission) => permission !== 'users'),
};

function asked(calls: Call[]): number {
  return calls.filter((call) => call.path === '/api/auth/me').length;
}

describe('доступ сменился посреди сеанса', () => {
  it('отказ с признаком перечитывает «кто я»: раздел уходит из меню, на месте — объяснение', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    let taken = false;
    const recorded = serve({
      'GET /api/auth/me': () => ({ body: taken ? WITHOUT_USERS : ADMIN }),
      'GET /api/users': () => {
        taken = true;
        return {
          status: 403,
          body: { detail: NO_USERS },
          headers: { 'X-Access-Refusal': 'permission' },
        };
      },
    });
    renderWith(<AppRoutes />, '/users');

    expect(await screen.findByText('Раздел недоступен')).toBeInTheDocument();
    const menu = within(screen.getByRole('navigation', { name: 'Разделы' }));
    expect(menu.queryByRole('link', { name: 'Учётки' })).not.toBeInTheDocument();
    expect(menu.getByRole('link', { name: 'Обзор' })).toBeInTheDocument();
    expect(asked(recorded.calls)).toBe(2);
  });

  it('«сначала смените разовый пароль» уводит на смену пароля, а не оставляет отказ', async () => {
    // Проверка прода 10.10.2026: после сброса открытый сеанс показывал «Доноры не загрузились
    // · Сначала смените разовый пароль: POST /api/auth/password» и никуда не вёл.
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    let reset = false;
    serve({
      'GET /api/auth/me': () => ({
        body: reset ? { ...OPERATOR, must_change_password: true } : OPERATOR,
      }),
      'GET /api/suppressions': () => {
        reset = true;
        return {
          status: 403,
          body: { detail: 'Сначала смените разовый пароль: пока он не сменён, остальное закрыто' },
          headers: { 'X-Access-Refusal': 'change-password' },
        };
      },
    });
    renderWith(<AppRoutes />, '/suppressions');

    expect(await screen.findByText('Смена пароля')).toBeInTheDocument();
    expect(screen.getByText(/Пароль выдан разовым/)).toBeInTheDocument();
  });

  it('отказ без признака — не отказ доступа: карточка не перечитывается', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({
      'GET /api/auth/me': { body: ADMIN },
      'GET /api/users': { status: 403, body: { detail: 'Forbidden' } },
    });
    renderWith(<AppRoutes />, '/users');

    expect(await screen.findByText('Список не загрузился')).toBeInTheDocument();
    expect(asked(recorded.calls)).toBe(1);
  });
});
