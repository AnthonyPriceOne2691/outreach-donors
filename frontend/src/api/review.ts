/**
 * Рассмотрение прогона: прогон кончается очередью, человек решает,
 * судья сортирует и подсказывает.
 */

import { request } from './client';
import type { AccuracyView, DecideResult, ReviewDecision, ReviewView } from './types';

export function loadReview(
  runId: number,
  status: ReviewDecision,
  showDoubtful: boolean,
): Promise<ReviewView> {
  const params = new URLSearchParams({ status });
  if (showDoubtful) params.set('show_doubtful', 'true');
  return request<ReviewView>(`/review/runs/${runId}?${params.toString()}`);
}

/** `pending` возвращает решение: на случай, если ошибся. */
export function decideCandidates(
  runId: number,
  candidateIds: number[],
  decision: ReviewDecision,
): Promise<DecideResult> {
  return request<DecideResult>(`/review/runs/${runId}/decide`, {
    method: 'POST',
    body: { candidate_ids: candidateIds, decision },
  });
}

export function loadAccuracy(runId?: number): Promise<AccuracyView> {
  return request<AccuracyView>(
    runId === undefined ? '/review/accuracy' : `/review/accuracy?run_id=${runId}`,
  );
}
