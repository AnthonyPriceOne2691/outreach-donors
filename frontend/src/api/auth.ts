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

export function changePassword(current: string, next: string): Promise<void> {
  return request<void>('/auth/password', {
    method: 'POST',
    body: { current, new: next },
  });
}
