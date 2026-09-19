/**
 * Записанные ответы вместо сервера.
 *
 * Тесты интерфейса не ходят в сеть и не поднимают базу: они проверяют,
 * что показано и что отправлено, а не то, что сервер умеет. Это разные
 * вопросы, и смешивать их значит получить набор, который краснеет от
 * миграции.
 *
 * **Запрос на незаписанный адрес — падение с внятным текстом.** Молчаливый
 * пустой ответ превратил бы опечатку в пути в «данные не пришли», а искать
 * её пришлось бы в компоненте.
 */

import { vi } from 'vitest';

export interface Answer {
  status?: number;
  body?: unknown;
  headers?: Record<string, string>;
}

export interface Call {
  method: string;
  path: string;
  body: unknown;
  token: string | null;
}

type Route = Answer | ((call: Call) => Answer);

export interface Recorded {
  /** Что именно ушло на сервер — по порядку. */
  calls: Call[];
}

export function serve(routes: Record<string, Route>): Recorded {
  const recorded: Recorded = { calls: [] };

  vi.spyOn(globalThis, 'fetch').mockImplementation(
    (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const path =
        typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
      const method = (init?.method ?? 'GET').toUpperCase();
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const call: Call = {
        method,
        path,
        body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
        token: headers['Authorization'] ?? null,
      };
      recorded.calls.push(call);

      const route = routes[`${method} ${path}`];
      if (route === undefined) {
        throw new Error(
          `Ответ на «${method} ${path}» не записан. Записаны: ${Object.keys(routes).join(', ')}`,
        );
      }

      const answer = typeof route === 'function' ? route(call) : route;
      const status = answer.status ?? 200;
      return Promise.resolve(
        new Response(status === 204 ? null : JSON.stringify(answer.body ?? null), {
          status,
          headers: { 'content-type': 'application/json', ...(answer.headers ?? {}) },
        }),
      );
    },
  );

  return recorded;
}
