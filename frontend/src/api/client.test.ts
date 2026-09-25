/**
 * Разбор ответов сервера. Проверяется то, от чего зависит поведение
 * экранов: тип отказа, текст отказа и судьба пропуска.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { AuthError, DeniedError, TooManyAttemptsError, request } from './client';
import { TOKEN_KEY } from '../test/fixtures';
import { misses, serve } from '../test/server';

describe('запрос к серверу', () => {
  beforeEach(() => localStorage.setItem(TOKEN_KEY, 'пропуск'));

  it('прикладывает пропуск', async () => {
    const recorded = serve({ 'GET /api/auth/me': { body: { id: 1 } } });

    await request('/auth/me');

    expect(recorded.calls[0]?.token).toBe('Bearer пропуск');
  });

  it('вход идёт без пропуска', async () => {
    const recorded = serve({ 'POST /api/auth/login': { body: {} } });

    await request('/auth/login', { method: 'POST', body: {}, anonymous: true });

    expect(recorded.calls[0]?.token).toBeNull();
  });

  it('204 не пытается разобрать тело', async () => {
    serve({ 'POST /api/auth/password': { status: 204 } });

    await expect(request('/auth/password', { method: 'POST', body: {} })).resolves.toBeUndefined();
  });

  it('401 выбрасывает пропуск: следующий запрос ушёл бы с тем же отказом', async () => {
    serve({
      'GET /api/auth/me': { status: 401, body: { detail: 'Пропуск просрочен — войдите заново' } },
    });

    await expect(request('/auth/me')).rejects.toThrow(AuthError);
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it('403 доносит название действия', async () => {
    serve({
      'GET /api/users': {
        status: 403,
        body: { detail: 'Действие «users» недоступно этой учётке' },
      },
    });

    await expect(request('/users')).rejects.toThrow(/users/);
    await expect(request('/users')).rejects.toBeInstanceOf(DeniedError);
  });

  it('429 доносит, сколько ждать', async () => {
    serve({
      'POST /api/auth/login': {
        status: 429,
        body: { detail: 'Слишком много попыток входа. Повторите через 59 с.' },
        headers: { 'Retry-After': '59' },
      },
    });

    const failure = await request('/auth/login', {
      method: 'POST',
      body: {},
      anonymous: true,
    }).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(TooManyAttemptsError);
    expect((failure as TooManyAttemptsError).retryAfterSeconds).toBe(59);
  });

  it('разбор тела запроса показывается первой причиной, а не схемой целиком', async () => {
    serve({
      'PATCH /api/users/1': {
        status: 422,
        body: { detail: [{ msg: 'Неизвестные действия: снд', loc: ['body', 'permissions'] }] },
      },
    });

    await expect(request('/users/1', { method: 'PATCH', body: {} })).rejects.toThrow(
      /Неизвестные действия/,
    );
  });

  it('незаписанный адрес роняет тест с внятным текстом', () => {
    serve({ 'GET /api/auth/me': { body: {} } });

    expect(() => fetch('/api/опечатка')).toThrow(/не записан/);
    // Промах ещё и запомнен: экран на TanStack Query проглатывает брошенное
    // как «не загрузилось», и тест роняет уже `afterEach`. Здесь промах
    // нарочный — забираем его, проверив.
    expect(misses.splice(0)).toEqual([expect.stringMatching(/опечатка/)]);
  });
});
