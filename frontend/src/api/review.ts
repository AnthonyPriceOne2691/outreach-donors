/**
 * Рассмотрение прогона: прогон кончается очередью, человек решает,
 * судья сортирует и подсказывает.
 */

import { request } from './client';
import type { DecideResult, ReviewDecision, ReviewView } from './types';

/** Совет судьи против решений человека: сколько советов, сколько с ними согласились. Здесь,
 *  а не в `types.ts`: тот у потолка длины, а числа нужны только рассмотрению прогона. */
export interface AgreementView {
  advised: number;
  agreed: number;
  precision: number | null;
}

/** Судья против человека: по этим числам решают, доверять ли судье больше. */
export interface AccuracyView {
  decided: number;
  by_advice: Record<string, AgreementView>;
  by_layer: Record<string, Record<string, AgreementView>>;
  by_intent: Record<string, Record<string, AgreementView>>;
  unjudged: number;
  asked_to_review: number;
  auto_accept_ready: boolean;
  auto_accept_precision: number;
  auto_accept_min_decisions: number;
}

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
