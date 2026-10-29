/**
 * Потолки модели на «Расходе» (слово Anthony 29.10.2026: «чтобы администратор мог поднимать
 * расходы сам»; устройство — как ключи в пространстве Ahrefs): общий потолок на весь сервис и у
 * каждого раздела свой лимит процентом общего, а что не роздано — свободный остаток. Всё —
 * числами сервера: лимит, потрачено и осталось — тем же счётом, что у сторожа.
 *
 * Своим файлом, а не в `settings.ts`: потолки модели — своя тема, как кап Ahrefs у сервера.
 */

import { request } from './client';

/** Разделы с лимитом: продажи, агент переписки и аутрич — всё остальное. */
export type LlmPart = 'sales' | 'agent' | 'outreach';

/** Блок числами: общий (`null` — без потолка) и лимиты разделов, проценты общего. */
export interface LlmBlock {
  general_tokens: number | null;
  sales_share: number;
  agent_share: number;
  outreach_share: number;
}

/** Смена блока: что поставили, кто и когда; `set_by` `null` — учётку удалили. */
export interface LlmCapChange {
  id: number;
  block: LlmBlock;
  set_by: string | null;
  set_at: string;
}

export interface LlmPartCard {
  part: LlmPart;
  title: string;
  /** Лимит процентом общего; `null` — блок не сохраняли, потолок — из настроек сервера. */
  share: number | null;
  /** Лимит, токены за сутки; `null` — без потолка. */
  limit: number | null;
  spent: number;
  left: number | null;
  used_percent: number | null;
}

export interface LlmCaps {
  /** `env` — блок не сохраняли, потолки из настроек сервера; `parts` — с экрана. */
  scheme: 'env' | 'parts';
  /** Общий на весь сервис, токены за сутки; `null` — без потолка. */
  general: number | null;
  /** Свободный остаток — процентами и токенами общего; `null` — не сохраняли. */
  free_share: number | null;
  free: number | null;
  parts: LlmPartCard[];
  /** Потрачено моделью за сутки — без права «Продажи» без продаж. */
  spent: number;
  /** С чего открывается окно: сохранённое, а не сохраняли — предложенное. */
  saved: LlmBlock | null;
  suggested: LlmBlock;
  general_presets: number[];
  share_step: number;
  day_start: string;
  resets_at: string;
  /** «через 5 ч 12 мин (в 03:00 по Москве)». */
  reset_in: string;
  changes: LlmCapChange[];
}

/** Новый блок — с этой минуты; право — «менять потолки расхода», лимит продаж — ещё и
 *  «Продажи». */
export function saveLlmCaps(block: LlmBlock): Promise<LlmCapChange> {
  return request<LlmCapChange>('/usage/llm-caps', { method: 'POST', body: block });
}
