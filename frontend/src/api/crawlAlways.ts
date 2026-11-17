/**
 * Обход 24/7 (`/crawls/always`, 17.11.2026): выключатель, порог «мало рекламодателей», переобход
 * слабых и пометка донора руками.
 *
 * Строку состояния и строки «i» считает сервер (`words`, `details`), экран их только называет.
 * Сколько сайтов получат пометку при другом пороге — экран считает сам по долям сервера (`yields`),
 * без запроса на каждую цифру в поле.
 *
 * Типы — своим файлом, а не в `crawls.ts` и не в общем `types.ts`: их читает только вкладка
 * «Обход доноров».
 */

import { request } from './client';

/** Сайты 24/7 по ступеням. */
export interface AlwaysCounts {
  /** Ни разу не обходились — их 24/7 берёт первыми. */
  new: number;
  /** Ещё не прошли первые 10 000 страниц. */
  first: number;
  /** Идут дальше 10 000. */
  deeper: number;
  /** Пропускаются как «мало рекламодателей». */
  weak: number;
  /** Пройдены до конца. */
  exhausted: number;
  /** Частей 24/7 идёт сейчас. */
  running: number;
  /** Сайтов в обходе. */
  total: number;
}

export interface AlwaysView {
  /** Рубильник окружения включён: без него выключателя нет. */
  available: boolean;
  enabled: boolean;
  /** Кто и когда менял настройки последним; не меняли ни разу — пусто. */
  set_at: string | null;
  set_by: string | null;
  /** Порог «мало рекламодателей»: меньше стольких процентов страниц с платной ссылкой. */
  threshold_pct: number;
  /** Переобход даже сайтов с пометкой «мало рекламодателей». */
  recrawl_weak: boolean;
  /** Почему включённый сейчас не идёт — словами; идёт или выключен — пусто. */
  waiting_for: string | null;
  counts: AlwaysCounts;
  /** Страниц пройдено обходом 24/7. */
  pages: number;
  /** Строка состояния — словами сервера. */
  words: string;
  /** Строки для «i». */
  details: string[];
  /** Доля страниц с платной ссылкой, %, у каждого сайта, прошедшего первые 10 000. */
  yields: number[];
  /** Когда пройдено всё; не пройдено — пусто. */
  done_at: string | null;
}

/** Что сменить: одно поле за нажатие, остальные не трогаются. */
export interface AlwaysChange {
  enabled?: boolean;
  threshold_pct?: number;
  recrawl_weak?: boolean;
}

/** Кто поставил пометку: сервис по порогу или человек руками. */
export type WeakBy = 'auto' | 'human';

/** Обход 24/7 у донора — поле `depth` строки `/crawls/donors`. */
export interface DonorDepth {
  /** Страниц пройдено обходом 24/7. */
  pages: number;
  /** Частей 24/7 у сайта. */
  parts: number;
  /** Доля страниц с платной ссылкой, %; ещё не считали — пусто. */
  yield_pct: number | null;
  weak: boolean;
  /** `auto` — по порогу, `human` — руками (и снятая руками пометка), пусто — не помечен. */
  weak_by: WeakBy | null;
  exhausted: boolean;
  running: boolean;
}

/** Пометка руками: `true` — пометить, `false` — снять, `null` — вернуть оценку сервису. */
export type WeakMark = boolean | null;

export const ALWAYS_KEY = ['crawl-always'] as const;

export function fetchAlways(): Promise<AlwaysView> {
  return request<AlwaysView>('/crawls/always');
}

export function setAlways(change: AlwaysChange): Promise<AlwaysView> {
  return request<AlwaysView>('/crawls/always', { method: 'POST', body: change });
}

export function markWeak(host: string, weak: WeakMark): Promise<DonorDepth> {
  return request<DonorDepth>('/crawls/always/weak', { method: 'POST', body: { host, weak } });
}
