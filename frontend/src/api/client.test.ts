/**
 * Разбор ответов сервера. Проверяется то, от чего зависит поведение
 * экранов: тип отказа, текст отказа и судьба пропуска.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { watchAccessDenied } from '../auth/session';
import {
  ApiError,
  AuthError,
  DeniedError,
  NO_CONNECTION,
  NoConnectionError,
  TooManyAttemptsError,
  download,
  request,
  upload,
} from './client';
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
    serve({ 'DELETE /api/threads/1/files/2': { status: 204 } });

    await expect(request('/threads/1/files/2', { method: 'DELETE' })).resolves.toBeUndefined();
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
        body: { detail: 'Действие «заводить учётки» не выдано этой учётке — права выдаёт админ' },
      },
    });

    await expect(request('/users')).rejects.toThrow(/«заводить учётки»/);
    await expect(request('/users')).rejects.toBeInstanceOf(DeniedError);
  });

  it('403 с признаком отказа доступа зовёт сессию перечитать «кто я», без признака — нет', async () => {
    // Проверка прода 10.10.2026: право сняли посреди сеанса, а меню вело в отказ.
    const denied = vi.fn();
    const stop = watchAccessDenied(denied);
    serve({
      'GET /api/users': {
        status: 403,
        body: { detail: 'Действие «заводить учётки» не выдано этой учётке — права выдаёт админ' },
        headers: { 'X-Access-Refusal': 'permission' },
      },
      // Отказ без признака — не наш отказ доступа (прокси, чужой обработчик).
      'GET /api/watchdog': { status: 403, body: { detail: 'Forbidden' } },
    });

    await expect(request('/users')).rejects.toBeInstanceOf(DeniedError);
    expect(denied).toHaveBeenCalledTimes(1);
    await expect(request('/watchdog')).rejects.toBeInstanceOf(DeniedError);
    expect(denied).toHaveBeenCalledTimes(1);
    stop();
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

  it('обрыв связи — словами, а не текстом браузера: у запроса, формы и выгрузки', async () => {
    // Проверка прода 10.10.2026: «Не завели · Failed to fetch». Место одно — на все формы.
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'));

    const failures = await Promise.all([
      request('/suppressions', { method: 'POST', body: {} }).catch((error: unknown) => error),
      upload('/sales/import/preview', new FormData()).catch((error: unknown) => error),
      download('/donors/export').catch((error: unknown) => error),
    ]);

    for (const failure of failures) {
      expect(failure).toBeInstanceOf(NoConnectionError);
      expect(failure).toHaveProperty('message', NO_CONNECTION);
      // Ответа не было — и кода ответа нет: это не отказ сервера.
      expect(failure).not.toBeInstanceOf(ApiError);
    }
  });

  it('отмена запроса — не обрыв: уходит как есть', async () => {
    const aborted = new DOMException('The operation was aborted.', 'AbortError');
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(aborted);

    const failure = await request('/donors').catch((error: unknown) => error);

    expect(failure).toBe(aborted);
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
