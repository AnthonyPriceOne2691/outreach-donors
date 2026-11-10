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
  /** Советы итога числами: пишем, не пишем, не понять. Нет или `null` — итог старше этих чисел,
   *  советы — только в `lines`. */
  advice?: Partial<Record<'write' | 'skip' | 'unclear', number>> | null;
  why: string | null;
}

export function fetchJudgePass(): Promise<JudgePassView> {
  return request<JudgePassView>('/advertisers/judge-pass');
}

/** `cases` — число из сметы, которую подтвердил человек: больше задача не возьмёт. */
export function startJudgePass(cases: number): Promise<JudgePassView> {
  return request<JudgePassView>('/advertisers/judge-pass', { method: 'POST', body: { cases } });
}

export const JUDGE_PASS_KEY = ['advertisers-judge-pass'] as const;

/** Разбор ещё идёт: опрашиваем ход, второй не запускаем. */
export function passMoving(view: JudgePassView | undefined): boolean {
  return view?.state === 'queued' || view?.state === 'running';
}

/** Разбор встал или оборвался: следующий запуск продолжает с места. */
export function passResumes(view: JudgePassView | undefined): boolean {
  return view?.state === 'stopped' || view?.state === 'failed';
}
