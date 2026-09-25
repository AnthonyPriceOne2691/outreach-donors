/** Ответы сервера, снятые с живого прогона 19.09.2026. */

import type { Me, OverviewView, SignedIn, UserCard } from '../api/types';
import type { Answer } from './server';

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

/** Сводка главной — числа с базы разработки 25.09.2026. */
export const OVERVIEW: OverviewView = {
  donors: {
    total: 1065,
    unchecked: 0,
    suitable: 840,
    accepted: 0,
    rejected: 0,
    with_email: 601,
    form_only: 97,
    written: 25,
    replied: 3,
    priced: 1,
    priced_fresh: 1,
  },
  waiting: { review: 394, review_runs: [18], prices: 1, leads: 0, forms: 97, advertisers: 0 },
  letters: {
    donors: { queued: 77, sent: 28, delivered: 7, bounced: 1 },
    advertisers: { queued: 0, sent: 0, delivered: 0, bounced: 0 },
  },
  last_run: {
    id: 18,
    status: 'done',
    country: 'us',
    keywords: 100,
    estimated_units: 14983,
    actual_units: 16998,
    estimate_error: 0.134,
    stats: null,
    started_at: '2026-09-23T16:21:00Z',
    alive_at: '2026-09-23T16:40:00Z',
    hosts: 535,
    reviewed: 0,
    disagreements: 0,
    queue: { pending: 394 },
  },
  ahrefs_units: 42716,
  ahrefs_cap: 100000,
  serp_usd: '0.7194',
  transport: { name: 'null', real: false, problem: null },
};

/** Главная сама ходит за сторожем тишины и сводкой. Тест, который попадает
 *  на неё (после входа, смены пароля, в раме), записывает оба ответа: промах
 *  мимо записанных роняет тест, даже если экран проглотил его молча. */
export const HOME_ROUTES: Record<string, Answer> = {
  'GET /api/watchdog': { body: { alarms: [] } },
  'GET /api/overview': { body: OVERVIEW },
};
