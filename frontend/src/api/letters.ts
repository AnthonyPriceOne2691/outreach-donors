import type {
  BuildLettersRequest,
  BuildQueued,
  LetterStage,
  LettersView,
  QueuedLetter,
  SendResult,
} from './types';
import { request } from './client';
import type { Stage } from './stages';
import type { ResolveOutcome, ResolvedLetter, UnknownLettersView } from './unknownOutcome';

/** Очередь этапа. Донорам — без параметра: это умолчание сервера. */
export function listLetters(stage: LetterStage = 'donors'): Promise<LettersView> {
  return request<LettersView>(stage === 'donors' ? '/letters' : `/letters?stage=${stage}`);
}

export function buildLetters(body: BuildLettersRequest): Promise<BuildQueued> {
  return request<BuildQueued>('/letters/build', { method: 'POST', body });
}

export function editLetter(
  id: number,
  body: { subject: string; body: string },
): Promise<QueuedLetter> {
  return request<QueuedLetter>(`/letters/${id}`, { method: 'PATCH', body });
}

export function skipLetter(id: number): Promise<QueuedLetter> {
  return request<QueuedLetter>(`/letters/${id}/skip`, { method: 'POST' });
}

export function sendLetter(id: number): Promise<SendResult> {
  return request<SendResult>(`/letters/${id}/send`, { method: 'POST' });
}

/** Пачка ушла в очередь задач: сколько писем ждёт в очереди этапа. */
export interface SendQueueQueued {
  job_id: string;
  queued: number;
}

/** Отправить всю очередь этапа пачкой (слово Anthony 06.10.2026). */
export function sendQueue(stage: Stage): Promise<SendQueueQueued> {
  return request<SendQueueQueued>('/letters/send-queue', { method: 'POST', body: { stage } });
}

/** Письма этапа с неизвестным исходом: зависли в «отправляется» дольше пяти минут. */
export function listUnknownLetters(stage: LetterStage = 'donors'): Promise<UnknownLettersView> {
  return request<UnknownLettersView>(
    stage === 'donors' ? '/letters/unknown' : `/letters/unknown?stage=${stage}`,
  );
}

/** Решение человека по журналу платформы: «ушло» или «вернуть в очередь». */
export function resolveLetter(id: number, outcome: ResolveOutcome): Promise<ResolvedLetter> {
  return request<ResolvedLetter>(`/letters/${id}/resolve`, { method: 'POST', body: { outcome } });
}
