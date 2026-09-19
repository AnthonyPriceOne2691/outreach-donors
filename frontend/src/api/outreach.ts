import type { SenderCard, SendersView, ThreadCard, ThreadView } from './types';
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
