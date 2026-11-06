/**
 * «Переобойти спорные» (`/advertisers/judge-pass`): смета, запуск, ход и итог. Слова считает сервер,
 * экран их только называет.
 */

import { request } from './client';

export interface JudgePassEstimate {
  cases: number;
  lines: string[];
  /** Почему не запустить — словами; `null` — можно. */
  refusal: string | null;
}

/** idle — разбора не было; queued, running — идёт; stopped — встал с причиной; failed — упал. */
export type JudgePassState =
  | 'idle'
  | 'queued'
  | 'running'
  | 'done'
  | 'stopped'
  | 'failed'
  | 'unknown';

export interface JudgePassView {
  estimate: JudgePassEstimate;
  state: JudgePassState;
  done: number;
  total: number;
  by: string | null;
  ended_at: string | null;
  lines: string[];
  why: string | null;
}

export function fetchJudgePass(): Promise<JudgePassView> {
  return request<JudgePassView>('/advertisers/judge-pass');
}

/** `cases` — число из сметы, которую подтвердил человек: больше задача не возьмёт. */
export function startJudgePass(cases: number): Promise<JudgePassView> {
  return request<JudgePassView>('/advertisers/judge-pass', { method: 'POST', body: { cases } });
}
