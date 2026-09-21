/** Ответы сервера, снятые с живого прогона 19.09.2026. */

import type { Me, SignedIn, UserCard } from '../api/types';

export const ADMIN: Me = {
  id: 1,
  email: 'админ@site.com',
  role: 'admin',
  permissions: ['prices', 'run', 'send', 'senders', 'settings', 'users', 'view'],
  must_change_password: false,
  last_login_at: '2026-09-19T10:24:00+00:00',
};

export const OPERATOR: Me = {
  id: 2,
  email: 'оператор@site.com',
  role: 'operator',
  permissions: ['prices', 'run', 'settings', 'view'],
  must_change_password: false,
  last_login_at: null,
};

export const NEWCOMER: Me = {
  ...OPERATOR,
  id: 3,
  email: 'новичок@site.com',
  must_change_password: true,
};

export function card(user: Me, extra: Partial<UserCard> = {}): UserCard {
  return { ...user, overrides: {}, is_active: true, ...extra };
}

export function signedIn(user: Me): SignedIn {
  return { token: 'пропуск.' + String(user.id), token_type: 'bearer', expires_in_hours: 24, user };
}

export const TOKEN_KEY = 'outreach_donors_token';
