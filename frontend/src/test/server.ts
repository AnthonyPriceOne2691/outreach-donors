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
  /** Тело как есть, не JSON: файл выгрузки. Тип — заголовком. */
  raw?: string;
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

/**
 * Незаписанные запросы, пойманные с начала теста.
 *
 * Бросить ошибку из `fetch` мало: запрос через TanStack Query превращает её
 * в состояние экрана («не загрузилось»), и тест, который это состояние не
 * проверяет, остаётся зелёным. Так 25.09.2026 прошли тесты главной, хотя
 * новый маршрут сводки в них записан не был. Поэтому промах ещё и
 * запоминается, а `afterEach` в `setup.ts` роняет по нему тест.
 */
export const misses: string[] = [];

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
        const miss = `Ответ на «${method} ${path}» не записан. Записаны: ${Object.keys(routes).join(', ')}`;
        misses.push(miss);
        throw new Error(miss);
      }

      const answer = typeof route === 'function' ? route(call) : route;
      const status = answer.status ?? 200;
      const body = status === 204 ? null : (answer.raw ?? JSON.stringify(answer.body ?? null));
      return Promise.resolve(
        new Response(body, {
          status,
          headers: { 'content-type': 'application/json', ...(answer.headers ?? {}) },
        }),
      );
    },
  );

  return recorded;
}
