/**
 * Кандидаты в рекламодатели: очередь ручной проверки и решение по ней.
 *
 * Экран нужен ради одного числа: допуск по ложным рекламодателям —
 * десять процентов, а скоринг судит по пяти признакам, два из которых
 * выведены из замера на одной нише.
 */

import { request } from './client';
import type { CandidateCard, CandidatesView } from './types';

/** Что человек смотрит списком: спорных — решить, «куплено» — проверить до письма. */
export type ReviewedVerdict = 'pending' | 'bought';

/** Кандидаты с вердиктом. С `includeDecided` — и те, по которым решили. */
export function fetchCandidates(
  includeDecided = false,
  verdict: ReviewedVerdict = 'pending',
): Promise<CandidatesView> {
  const params = new URLSearchParams();
  if (includeDecided) params.set('include_decided', 'true');
  if (verdict !== 'pending') params.set('verdict', verdict);
  const query = params.size > 0 ? `?${params.toString()}` : '';
  return request<CandidatesView>(`/advertisers${query}`);
}

/**
 * Решение человека. `force` нужен, чтобы передумать: без него повторное
 * решение — отказ, иначе два человека в одной очереди затрут друг друга.
 */
export function decideCandidate(
  id: number,
  confirmed: boolean,
  force = false,
): Promise<CandidateCard> {
  return request<CandidateCard>(`/advertisers/${id}/decide`, {
    method: 'POST',
    body: { confirmed, force },
  });
}
