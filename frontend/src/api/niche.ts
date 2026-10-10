/**
 * Бизнесы ниши из выдачи прогона — кандидаты в рекламодатели
 * (`/api/advertisers/niche`): сайты, которые сами продают в нише прогона.
 *
 * Типы лежат здесь, а не в `types.ts`: тот уже за пределом длины файла.
 */

import { request } from './client';

/** Бизнес ниши: по чему человек решает «пишем / не пишем». */
export interface NicheCard {
  id: number;
  host: string;
  run_id: number | null;
  keywords: string[];
  country: string | null;
  /** Цитата судьи из выдачи — по ней видно, что сайт продаёт своё. */
  quote: string | null;
  /** Кто сказал «продаёт своё»: человек или судья. */
  intent_by: 'human' | 'judge';
  /** `null` — ещё не решали. */
  confirmed: boolean | null;
  decided_by: string | null;
  decided_at: string | null;
}

/** Страница бизнесов ниши: `total` — строк по всем страницам, `limit` — размер
 *  страницы, его называет сервер (как у очереди форм). */
export interface NicheView {
  rows: NicheCard[];
  waiting: number;
  total: number;
  page: number;
  limit: number;
}

export interface NicheCollected {
  run_id: number;
  found: number;
  added: number;
}

export function fetchNiche(page: number): Promise<NicheView> {
  return request<NicheView>(`/advertisers/niche?page=${page}`);
}

/** «Пишем» открывает поиск адреса и письмо; «не пишем» запоминается. */
export function decideNiche(id: number, write: boolean): Promise<NicheCard> {
  return request<NicheCard>(`/advertisers/niche/${id}/decide`, {
    method: 'POST',
    body: { write },
  });
}

/** Собрать бизнесы ниши из прогона, прошедшего до сбора. */
export function collectNiche(runId: number): Promise<NicheCollected> {
  return request<NicheCollected>(`/advertisers/niche/collect?run_id=${runId}`, {
    method: 'POST',
  });
}
