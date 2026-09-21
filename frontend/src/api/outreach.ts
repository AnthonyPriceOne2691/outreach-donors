import type {
  Reviewed,
  ReviewPrice,
  SenderCard,
  SendersView,
  ThreadCard,
  ThreadView,
} from './types';
import { request } from './client';

export function listSenders(): Promise<SendersView> {
  return request<SendersView>('/senders');
}

export function enableSender(id: number): Promise<SenderCard> {
  return request<SenderCard>(`/senders/${id}/enable`, { method: 'POST' });
}

export function disableSender(id: number, reason: string): Promise<SenderCard> {
  return request<SenderCard>(`/senders/${id}/disable`, { method: 'POST', body: { reason } });
}

export function listThreads(): Promise<ThreadCard[]> {
  return request<ThreadCard[]>('/threads');
}

export function fetchThread(id: number): Promise<ThreadView> {
  return request<ThreadView>(`/threads/${id}`);
}

/** Подтвердить разбор цены человеком. Его решение сильнее любой
 *  уверенности модели и кладёт цену в карточку донора. */
export function reviewReply(id: number, body: ReviewPrice): Promise<Reviewed> {
  return request<Reviewed>(`/replies/${id}`, { method: 'PATCH', body });
}
