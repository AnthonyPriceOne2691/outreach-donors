/**
 * Кандидаты в рекламодатели: очередь ручной проверки и решение по ней.
 *
 * Экран нужен ради одного числа: допуск по ложным рекламодателям —
 * десять процентов, а скоринг судит по пяти признакам, два из которых
 * выведены из замера на одной нише.
 */

import { request } from './client';
import type { CandidateCard, CandidatesView } from './types';

/** Пограничные кандидаты. С `includeDecided` — и те, по которым решили. */
export function fetchCandidates(includeDecided = false): Promise<CandidatesView> {
  const query = includeDecided ? '?include_decided=true' : '';
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
