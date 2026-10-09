/**
 * Рама: меню по правам и выход.
 *
 * Пункт меню, ведущий в отказ, — это обещание, которого интерфейс
 * не сдержит; проверяется, что оператор его не видит. У пунктов — числа
 * ждущей работы; кто вошёл, роль и права — в меню у почты в шапке.
 */

import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { AppRoutes } from '../App';
import { permissionTitle, ROLE_TITLES } from '../api/labels';
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

/** Пункт меню целиком — с числом ждущей работы, если оно есть. */
function item(title: string): HTMLElement {
  const link = within(screen.getByRole('navigation')).getByText(title).closest('a');
  if (link === null) throw new Error(`пункта «${title}» нет`);
  return link;
}

const WORK = { run: 3, threads: 2, forms: 0, advertisers: 12 };

describe('числа ждущей работы у пунктов меню', () => {
  it('стоят у своих разделов, ноль не рисуется', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: OPERATOR },
      'GET /api/overview/work': { body: WORK },
      ...HOME_ROUTES,
    });
    renderWith(<AppRoutes />, '/');

    await waitFor(() => expect(item('Прогон')).toHaveTextContent('3 ждут человека'));
    expect(item('Диалоги')).toHaveTextContent('2 ждут человека');
    expect(item('Рекламодатели')).toHaveTextContent('12 ждут человека');
    expect(item('Формы')).not.toHaveTextContent('ждут человека');
    expect(item('Доноры')).not.toHaveTextContent('ждут человека');
  });

  it('без права смотреть базу чисел не спрашивает', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    const recorded = serve({
      'GET /api/auth/me': { body: { ...OPERATOR, permissions: ['sales'] } },
      'GET /api/overview/work': { body: WORK },
      ...HOME_ROUTES,
    });
    renderWith(<AppRoutes />, '/');

    await screen.findByText(/Вошли как/);
    expect(recorded.calls.map((call) => call.path)).not.toContain('/api/overview/work');
  });

  it('перечитываются при переходе, а не через минуту', async () => {
    let asked = 0;
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({
      'GET /api/auth/me': { body: OPERATOR },
      'GET /api/overview/work': () => {
        asked += 1;
        return { body: { ...WORK, run: asked } };
      },
      ...HOME_ROUTES,
    });
    renderWith(<AppRoutes />, '/settings/extra');
    await waitFor(() => expect(item('Прогон')).toHaveTextContent('1 ждут человека'));

    await userEvent.setup().click(item('Обзор'));

    await waitFor(() => expect(item('Прогон')).toHaveTextContent('2 ждут человека'));
  });
});

describe('меню у почты', () => {
  it('«Вошли как» — в шапке, роль, права и смена пароля — в её меню', async () => {
    localStorage.setItem(TOKEN_KEY, 'пропуск');
    serve({ 'GET /api/auth/me': { body: OPERATOR }, ...HOME_ROUTES });
    renderWith(<AppRoutes />, '/');
    // Пока меню закрыто, ни прав, ни смены пароля на экране нет: карточка
    // «Вошли как» ушла с «Обзора» (аудит экранов 09.10.2026).
    const account = await screen.findByRole('button', { name: `Вошли как ${OPERATOR.email}` });
    expect(screen.queryByText('Сменить пароль')).not.toBeInTheDocument();

    await userEvent.setup().click(account);

    const menu = await screen.findByRole('menu');
    expect(within(menu).getByText(ROLE_TITLES.operator)).toBeInTheDocument();
    expect(within(menu).getByText(`Права · ${OPERATOR.permissions.length}`)).toBeInTheDocument();
    expect(within(menu).getByText(permissionTitle('prices'))).toBeInTheDocument();
    // Выпадающее Mantine в jsdom для запросов по роли «скрыто» — переход не доигран.
    const password = within(menu).getByRole('menuitem', { name: 'Сменить пароль', hidden: true });
    expect(password).toHaveAttribute('href', '/password');
  });
});
