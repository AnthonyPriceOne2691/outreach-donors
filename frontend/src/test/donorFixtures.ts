/**
 * Записанные ответы экрана доноров и карточки донора — для тестов, заведённых
 * 26.09.2026 (фильтры под каждой колонкой, отметки и выгрузка, адреса руками).
 *
 * Числа здесь сходятся между собой: сводка вердиктов — это те же доноры, что
 * в строках и в счётчиках стран и данных. Урок демо-данных: число, назначенное
 * рядом с тем, из чего оно считается, однажды с ним разойдётся.
 */

import type { DonorFullCard, DonorRowCard, DonorsPage } from '../api/types';

export const ROW: DonorRowCard = {
  id: 1,
  host: 'good.example.test',
  status: 'suitable',
  reject_reason: null,
  dr: 45,
  org_traffic: 1_600_000_000,
  geo: 'us',
  geo_top_share: 0.62,
  contacts: 2,
  contact_status: 'found',
  last_price: null,
  last_price_currency: null,
  metrics_refreshed_at: '2026-09-18T10:00:00+00:00',
  fresh: true,
  freshness: 'fresh',
};

/** Три донора: из США, из Германии и без данных. */
export const ROWS: DonorRowCard[] = [
  ROW,
  {
    ...ROW,
    id: 2,
    host: 'weak.example.test',
    status: 'unsuitable',
    reject_reason: 'DR 8 ниже 20',
    dr: 8,
    org_traffic: 300,
    geo: 'de',
    geo_top_share: 0.4,
    contacts: 0,
    contact_status: null,
    metrics_refreshed_at: '2026-06-01T10:00:00+00:00',
    fresh: false,
    freshness: 'stale',
  },
  {
    ...ROW,
    id: 3,
    host: 'blank.example.test',
    status: 'unchecked',
    dr: null,
    org_traffic: null,
    geo: null,
    geo_top_share: null,
    contacts: 0,
    contact_status: null,
    metrics_refreshed_at: null,
    fresh: false,
    freshness: 'never',
  },
];

export function page(overrides: Partial<DonorsPage> = {}): DonorsPage {
  return {
    rows: ROWS,
    total: 3,
    counts: { suitable: 1, unsuitable: 1, unchecked: 1 },
    countries: { us: 1, de: 1 },
    freshness: { fresh: 1, stale: 1, never: 1 },
    export_limit: 10_000,
    waiting: { domains: 0, runs: [] },
    ...overrides,
  };
}

export const CONTACTS_STATE = { pending: 0, running: false, job_id: null, last: null, workers: 1 };

export const CARD: DonorFullCard = {
  id: 7,
  host: 'card.example.test',
  status: 'suitable',
  reject_reason: null,
  dr: 55,
  org_traffic: 120_000,
  geo: 'us',
  geo_top_share: 0.62,
  geo_breakdown: null,
  geo_partial: false,
  metrics: null,
  metrics_refreshed_at: '2026-09-18T10:00:00+00:00',
  expires_at: '2026-12-17T10:00:00+00:00',
  fresh: true,
  freshness: 'fresh',
  contact_status: null,
  contact_attempted_at: null,
  last_price: null,
  last_price_currency: null,
  last_price_at: null,
  contacts: [],
  contact_refusal: null,
  review: 'accepted',
  review_run: 18,
  letter_contact_id: null,
  letter_blocked: null,
};
