import type { AccessPatch, OneTimePassword, Role, UserCard } from './types';
import { request } from './client';

export function listUsers(): Promise<UserCard[]> {
  return request<UserCard[]>('/users');
}

export function createUser(email: string, role: Role): Promise<OneTimePassword> {
  return request<OneTimePassword>('/users', { method: 'POST', body: { email, role } });
}

/** Правка доступа — с паролем админа, который правит (аудит 20.11.2026): без него сервер
 *  отвечает «Неверный пароль — подтвердите действие своим паролем» и не меняет ничего. */
export function patchUser(id: number, patch: AccessPatch, password: string): Promise<UserCard> {
  return request<UserCard>(`/users/${id}`, {
    method: 'PATCH',
    body: { ...patch, current_password: password },
  });
}

/** Сброс чужого пароля — тоже с паролем админа. */
export function resetPassword(id: number, password: string): Promise<OneTimePassword> {
  return request<OneTimePassword>(`/users/${id}/password`, {
    method: 'POST',
    body: { current_password: password },
  });
}

/** Сбросить чужой вход с кодом — тоже с паролем админа (23.11.2026): телефон потерян. */
export function resetCode(id: number, password: string): Promise<UserCard> {
  return request<UserCard>(`/users/${id}/totp/reset`, {
    method: 'POST',
    body: { current_password: password },
  });
}
