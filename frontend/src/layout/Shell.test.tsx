/**
 * Рама: меню по правам и выход.
 *
 * Пункт меню, ведущий в отказ, — это обещание, которого интерфейс
 * не сдержит; проверяется, что оператор его не видит.
 */

import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { ADMIN, HOME_ROUTES, OPERATOR, TOKEN_KEY } from '../test/fixtures';
import { renderWith } from '../test/render';
import { serve } from '../test/server';

describe('рама приложения', () => {
  it('оператор не видит раздела учёток', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: OPERATOR }, ...HOME_ROUTES });
    renderWith(<AppRoutes />, '/');

    await screen.findByText(/Вошли как/);
    expect(screen.queryByText('Учётки')).not.toBeInTheDocument();
  });

  it('админ видит', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: ADMIN }, ...HOME_ROUTES });
    renderWith(<AppRoutes />, '/');

    expect(await screen.findByText('Учётки')).toBeInTheDocument();
  });

  it('выход убирает пропуск и возвращает на вход', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: ADMIN }, ...HOME_ROUTES });
    renderWith(<AppRoutes />, '/');
    await screen.findByText('Учётки');

    await userEvent.setup().click(screen.getByRole('button', { name: 'Выйти' }));

    expect(await screen.findByText('Вход для сотрудников')).toBeInTheDocument();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it('неизвестный адрес — внутри рамы, с объяснением и дорогой на главную', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: ADMIN } });
    renderWith(<AppRoutes />, '/settings/extra');

    expect(await screen.findByRole('heading', { name: 'Такой страницы нет' })).toBeInTheDocument();
    expect(screen.getByText('/settings/extra')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'На главную' })).toHaveAttribute('href', '/');
    // Рама на месте: меню и выход, а не пустое полотно.
    expect(screen.getByText('Учётки')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Выйти' })).toBeInTheDocument();
  });

  it('гостя неизвестный адрес ведёт на вход, а не на объяснение', async () => {
    serve({ 'GET /api/auth/me': { status: 401, body: { detail: 'Нужен вход' } } });
    renderWith(<AppRoutes />, '/no-such-page');

    expect(await screen.findByText('Вход для сотрудников')).toBeInTheDocument();
    expect(screen.queryByText('Такой страницы нет')).not.toBeInTheDocument();
  });
});
