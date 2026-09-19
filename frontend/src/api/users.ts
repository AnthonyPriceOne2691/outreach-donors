import type { AccessPatch, OneTimePassword, Role, UserCard } from './types';
import { request } from './client';

export function listUsers(): Promise<UserCard[]> {
  return request<UserCard[]>('/users');
}

export function createUser(email: string, role: Role): Promise<OneTimePassword> {
  return request<OneTimePassword>('/users', { method: 'POST', body: { email, role } });
}

export function patchUser(id: number, patch: AccessPatch): Promise<UserCard> {
  return request<UserCard>(`/users/${id}`, { method: 'PATCH', body: patch });
}

export function resetPassword(id: number): Promise<OneTimePassword> {
  return request<OneTimePassword>(`/users/${id}/password`, { method: 'POST' });
}
