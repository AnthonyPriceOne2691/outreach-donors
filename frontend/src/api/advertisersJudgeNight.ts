/**
 * «Судья сам, каждую ночь» (`/advertisers/judge-night`): как стоят полосы автоприменения, итог
 * последней ночи и переключатель полосы. Числа и строки итога считает сервер
 * (`crawl/judge_night_view.py`), экран их только называет.
 */

import type { JudgeBand } from './advertisersJudgeApply';
import { request } from './client';

/** Автоприменение полосы: включено ли и кто так поставил; не ставили ни разу — выключено. */
export interface JudgeBandState {
  band: JudgeBand;
  enabled: boolean;
  set_by: string | null;
  set_at: string | null;
}

/** Итог последней ночи. `applied` — решено самой ночью по полосам; полосы нет — она была
 *  выключена. `going` — шаг ещё идёт и что делает; `why` — почему не дошёл до конца или разбор
 *  встал. `lines` — строки для «i», как их отдал сервер. */
export interface JudgeNightSummary {
  /** Дата ночи по Москве: «2026-11-12». */
  night: string;
  status: string;
  judged: number;
  applied: Partial<Record<JudgeBand, number>>;
  unclear: number;
  going: string | null;
  why: string | null;
  lines: string[];
}

export interface JudgeNight {
  bands: JudgeBandState[];
  last: JudgeNightSummary | null;
  /** Шаг этой даты уже начинался: полоса, включённая сейчас, пойдёт следующей ночью. */
  ran_today: boolean;
  /** Доменов ждут «Перевести»: ночь этого не делает. */
  awaiting_promotion: number;
  /** Не больше стольких пар на полосу за ночь. */
  cap: number;
}

export function fetchJudgeNight(): Promise<JudgeNight> {
  return request<JudgeNight>('/advertisers/judge-night');
}

export function setJudgeBand(band: JudgeBand, enabled: boolean): Promise<JudgeNight> {
  return request<JudgeNight>('/advertisers/judge-night/bands', {
    method: 'POST',
    body: { band, enabled },
  });
}

/** Под ключом очереди проверки: решение и применённая порция перечитывают и «ждут перевода». */
export const JUDGE_NIGHT_KEY = ['advertisers', 'judge-night'] as const;
