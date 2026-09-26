/**
 * Отбор: кто принят, кто отклонён, кем и почему — и решение человека.
 *
 * Решение человека — тип сайта, а не «годен / не годен»: так расхождение
 * с машиной считается по каждому слою судьи отдельно.
 */

import { request } from './client';
import type {
  HumanIntent,
  SelectionAnswer,
  SelectionCard,
  SelectionHuman,
  SelectionJudge,
  SelectionTab,
  SelectionThresholds,
  SelectionView,
} from './types';

/** Вкладка, фильтры под колонками и страница. Размера страницы здесь нет:
 *  его называет сервер и возвращает в ответе (`limit`). */
export interface SelectionQuery {
  tab: SelectionTab;
  search?: string;
  thresholds?: SelectionThresholds;
  judge?: SelectionJudge;
  answer?: SelectionAnswer;
  human?: SelectionHuman;
  page?: number;
}

export function listSelection(query: SelectionQuery): Promise<SelectionView> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== '') params.set(key, String(value));
  }
  return request<SelectionView>(`/selection?${params.toString()}`);
}

/** `intent: null` снимает своё решение — на случай, если ошибся. */
export function decideSite(
  domainId: number,
  intent: HumanIntent | null,
  note?: string,
): Promise<SelectionCard> {
  return request<SelectionCard>(`/selection/${domainId}/decide`, {
    method: 'POST',
    body: { intent, note: note ?? null },
  });
}
