import type {
  BuildLettersRequest,
  BuildQueued,
  LetterStage,
  LettersView,
  QueuedLetter,
  SendResult,
} from './types';
import { request } from './client';

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
