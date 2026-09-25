/**
 * Один способ сходить на сервер.
 *
 * Отказы сервера разобраны по типам, а не по тексту: разбор текста
 * ломается от первой же правки сообщения, а показать человеку надо
 * разное. «Пропуск просрочен» — это вход заново, «слишком много
 * попыток» — подождать, «нет права» — сказать, какого именно.
 *
 * Текст отказа берётся из `detail` и показывается целиком: сообщения
 * сервера написаны так, чтобы говорить, что делать, и заменять их
 * на «ошибка запроса» значит выбрасывать ровно ту часть, ради которой
 * они писались.
 */

import { clearToken, readToken } from '../auth/session';

/** Ответ сервера с внятным отказом. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
  }
}

/** Пропуска нет, он просрочен или учётку отключили. */
export class AuthError extends ApiError {
  constructor(detail: string) {
    super(401, detail);
    this.name = 'AuthError';
  }
}

/** Действие недоступно этой учётке. */
export class DeniedError extends ApiError {
  constructor(detail: string) {
    super(403, detail);
    this.name = 'DeniedError';
  }
}

/** Попытки входа кончились. Сервер говорит, через сколько повторить. */
export class TooManyAttemptsError extends ApiError {
  readonly retryAfterSeconds: number | null;

  constructor(detail: string, retryAfterSeconds: number | null) {
    super(429, detail);
    this.name = 'TooManyAttemptsError';
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

/** Текст отказа для экрана: сообщение сервера целиком, а если его нет —
 *  честное «без объяснения», а не пустое место. */
export function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH';
  body?: unknown;
  /** Вход — единственный запрос без пропуска. */
  anonymous?: boolean;
}

async function detailOf(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === 'object' && 'detail' in body) {
      const { detail } = body;
      if (typeof detail === 'string') return detail;
      // Отказ разбора тела запроса приходит списком — показываем первый:
      // человеку нужна причина, а не полный разбор схемы.
      if (Array.isArray(detail) && detail.length > 0) {
        const first: unknown = detail[0];
        if (first && typeof first === 'object' && 'msg' in first) {
          return String(first.msg);
        }
      }
    }
  } catch {
    // Тело не разобралось — значит, отвечал не наш обработчик ошибок.
  }
  return `Сервер ответил ${response.status}`;
}

function retryAfterOf(response: Response): number | null {
  const header = response.headers.get('Retry-After');
  if (!header) return null;
  const seconds = Number.parseInt(header, 10);
  return Number.isFinite(seconds) ? seconds : null;
}

async function raise(response: Response): Promise<never> {
  const detail = await detailOf(response);
  if (response.status === 401) {
    // Пропуск больше не действует — хранить его незачем: иначе следующий
    // запрос уйдёт с ним же и получит тот же отказ.
    clearToken();
    throw new AuthError(detail);
  }
  if (response.status === 403) throw new DeniedError(detail);
  if (response.status === 429) throw new TooManyAttemptsError(detail, retryAfterOf(response));
  throw new ApiError(response.status, detail);
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, anonymous = false } = options;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers['content-type'] = 'application/json';
  if (!anonymous) {
    const token = readToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`/api${path}`, {
    method,
    headers,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });

  if (!response.ok) await raise(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
