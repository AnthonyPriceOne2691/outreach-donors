import type { Me, SignedIn } from './types';
import { request } from './client';

export function login(email: string, password: string): Promise<SignedIn> {
  return request<SignedIn>('/auth/login', {
    method: 'POST',
    body: { email, password },
    anonymous: true,
  });
}

export function fetchMe(): Promise<Me> {
  return request<Me>('/auth/me');
}

/** Смена своего пароля. Ответ — новый пропуск с карточкой, как у входа: пропуска,
 *  выданные до смены, сервер больше не принимает (проверка прода 10.10.2026). */
export function changePassword(current: string, next: string): Promise<SignedIn> {
  return request<SignedIn>('/auth/password', {
    method: 'POST',
    body: { current, new: next },
  });
}
