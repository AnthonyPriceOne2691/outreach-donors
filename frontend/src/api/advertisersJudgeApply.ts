/**
 * «Применить советы судьи» (`/advertisers/judge-apply`): итог очереди, план полосы, применение
 * порцией, партии и откат. Числа и слова правил считает сервер (`crawl/judge_apply.py`), экран их
 * только называет.
 */

import { request } from './client';

/** Полоса — совет судьи, который применяется решением. «Не понять» полосой не бывает. */
export type JudgeBand = 'skip' | 'write';

/** Что судья сказал очереди спорных: каждая ждущая пара — ровно в одном числе. `write` — «пишем»
 *  с известным DR, `waiting_dr` — «пишем», которое ждёт DR; `stale` — суд прежней подсказкой. */
export interface JudgeStanding {
  waiting: number;
  skip: number;
  write: number;
  waiting_dr: number;
  unclear: number;
  stale: number;
  without: number;
}

export interface JudgeRuleCount {
  rule: string;
  /** Правило словами таблицы судьи. */
  words: string;
  pairs: number;
}

export interface JudgeExample {
  donor_host: string;
  target_root: string;
  points: number;
  rule: string;
  /** Что сказал судья — словами. */
  words: string;
}

/** Что «пишем» даст переводу: доменов в полосе, новых среди них и у скольких новых адрес есть. */
export interface JudgeLaunch {
  domains: number;
  fresh: number;
  with_address: number;
}

export interface JudgePlan {
  band: JudgeBand;
  pairs: number;
  donors: number;
  rules: JudgeRuleCount[];
  examples: JudgeExample[];
  standing: JudgeStanding;
  /** «Не пишем»: у стольких доменов снимется рекламодатель. Обычно ноль. */
  takes_off: number;
  /** Только у «пишем». */
  launch: JudgeLaunch | null;
  /** Пар за один запрос применения. */
  portion: number;
}

/** Итог порции. `left` не ноль — экран шлёт следующую порцию той же партии. */
export interface JudgeApplied {
  batch: string;
  band: JudgeBand;
  applied: number;
  /** Пропущены: пока шла порция, пару решил человек. */
  decided: number;
  /** Пропущены: строку заменил новый обход. */
  gone: number;
  left: number;
  removed: number;
  stopped: number;
  /** Домены, которые уже ответили, — не больше двадцати; сколько всего — `answered_total`. */
  answered: string[];
  answered_total: number;
}

export interface JudgeReverted {
  batch: string;
  returned: number;
  /** «Пишем» осталось в силе: письмо домену уже ушло. */
  kept: number;
  /** Пары, которых последний обход не нашёл: вернутся нерешёнными. */
  forgotten: number;
  removed: number;
  stopped: number;
}

export interface JudgeBatch {
  id: string;
  band: string;
  by: string | null;
  started_at: string;
  applied: number;
  /** Не ноль — партию оборвали посреди полосы. */
  left: number;
  /** Решений партии в силе сейчас. */
  standing: number;
  reverted_at: string | null;
  reverted_by: string | null;
  returned: number;
}

export function fetchJudgeStanding(): Promise<JudgeStanding> {
  return request<JudgeStanding>('/advertisers/judge-apply');
}

export function fetchJudgePlan(band: JudgeBand): Promise<JudgePlan> {
  return request<JudgePlan>(`/advertisers/judge-apply/plan?band=${band}`);
}

/** Порция полосы. `batch` пуст — партию начинает сервер и называет её в ответе. */
export function applyJudgeAdvice(
  band: JudgeBand,
  batch: string | null,
  limit: number,
): Promise<JudgeApplied> {
  return request<JudgeApplied>('/advertisers/judge-apply', {
    method: 'POST',
    body: batch === null ? { band, limit } : { band, batch, limit },
  });
}

export function fetchJudgeBatches(): Promise<JudgeBatch[]> {
  return request<JudgeBatch[]>('/advertisers/judge-apply/batches');
}

export function revertJudgeBatch(batch: string): Promise<JudgeReverted> {
  return request<JudgeReverted>(
    `/advertisers/judge-apply/batches/${encodeURIComponent(batch)}/revert`,
    { method: 'POST' },
  );
}

/** Под ключом очереди проверки (`['advertisers']`): решение человека, конец разбора спорных и
 *  применённая порция перечитывают очередь — и числа советов вместе с ней. */
export const JUDGE_APPLY_KEY = ['advertisers', 'judge-apply'] as const;
