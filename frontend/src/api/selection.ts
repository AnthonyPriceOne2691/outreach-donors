/**
 * Отбор: кто принят, кто отклонён, кем и почему — и решение человека.
 *
 * Решение человека — тип сайта, а не «годен / не годен»: так расхождение
 * с машиной считается по каждому слою судьи отдельно.
 */

import { request } from './client';
import type {
  HumanIntent,
  JudgeDecider,
  SelectionCard,
  SelectionTab,
  SelectionView,
} from './types';

export interface SelectionQuery {
  tab: SelectionTab;
  search?: string;
  decided_by?: JudgeDecider;
  only_disagreements?: boolean;
  only_unreviewed?: boolean;
  only_unjudged?: boolean;
  limit?: number;
  offset?: number;
}

export function listSelection(query: SelectionQuery): Promise<SelectionView> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== '' && value !== false) params.set(key, String(value));
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
