/**
 * Автокруг рекламодателей (`/advertisers/auto-round`): выключатель, суточный потолок писем и итог
 * последнего круга. Слова итога считает сервер (`crawl/auto_round_view.py`), экран их только
 * называет; время — по часам смотрящего.
 */

import { request } from './client';

/** Выключатель как он стоит. Не ставили ни разу — выключен, потолок по умолчанию. */
export interface AutoRoundSwitch {
  enabled: boolean;
  /** Суточный потолок писем: столько круг собирает за сутки, не больше. */
  letters: number;
  set_by: string | null;
  set_at: string | null;
}

/** Последний круг. `words` — строка после «Автокруг 17.11, 08:12:»: итог, что делается или почему
 *  встал. `at` — когда кончился или начался, если ещё идёт. `lines` — строки для «i». */
export interface AutoRoundLast {
  /** Дата круга по Москве: «2026-11-17». */
  day: string;
  status: string;
  at: string | null;
  words: string;
  going: boolean;
  lines: string[];
}

export interface AutoRound {
  /** Рубильник сервиса включён: без него выключателя нет. */
  available: boolean;
  switch: AutoRoundSwitch;
  last: AutoRoundLast | null;
  /** Круг этой даты уже был: включённый сейчас пойдёт завтра. */
  ran_today: boolean;
  /** Когда пойдёт первый круг, если включить сейчас, — словами. */
  first: string;
  /** Границы суточного потолка — сервера, а не копия экрана. */
  letters_min: number;
  letters_max: number;
}

export function fetchAutoRound(): Promise<AutoRound> {
  return request<AutoRound>('/advertisers/auto-round');
}

export function setAutoRound(enabled: boolean, letters: number): Promise<AutoRound> {
  return request<AutoRound>('/advertisers/auto-round', {
    method: 'POST',
    body: { enabled, letters },
  });
}

export const AUTO_ROUND_KEY = ['advertisers-auto-round'] as const;
