/**
 * Рекламодатели списком и файлом (сверка ТЗ 11.10.2026: выгрузка была только у лидов).
 *
 * Типы — здесь, рядом с запросами, а не в общем `types.ts`: их читает только экран
 * рекламодателей, а `types.ts` у потолка длины.
 */

import { download, request, withQuery } from './client';
import type { Downloaded } from './client';
import type { ContactStatus } from './types';

/** Есть ли куда писать: адрес в базе, только форма или ничего. */
export type AddressState = 'found' | 'form' | 'none';

/** Что с оффером домену — одно слово по старшинству (`crawl/offer_state.py`). */
export type OfferState =
  | 'unsubscribed'
  | 'replied'
  | 'sent'
  | 'queued'
  | 'bounced'
  | 'stopped'
  | 'none';

export type AdvertiserSource = 'links' | 'niche';

/** Строка списка: кто, под какую ссылку, есть ли адрес и что с оффером. */
export interface AdvertiserCard {
  id: number;
  host: string;
  source: AdvertiserSource;
  /** Прогон, в выдаче которого нашёлся бизнес ниши; у рекламодателя по ссылке — `null`. */
  run_id: number | null;
  /** Донор, на котором нашли ссылку; у бизнеса ниши — `null`. */
  donor_host: string | null;
  page_url: string | null;
  anchor: string | null;
  points: number;
  /** За что балл — причины скоринга словами: «rel=sponsored +5». У бизнеса ниши — пусто. */
  reasons: string[];
  /** Пояснение причины по её словам — как у очереди проверки; нет поля — сервер старше экрана. */
  reason_notes?: Record<string, string>;
  /** Балл той же ссылки после пересчёта весов, если он не тот, с которым перевели. */
  rescored: number | null;
  donors: number;
  /** Исход последнего поиска адреса; `null` — не искали. */
  contact_status: ContactStatus | null;
  address: AddressState;
  letter: OfferState;
}

/** Счётчики под фильтрами — по всему списку, а не по фильтру: своим запросом, который
 *  экран не повторяет на смену страницы и фильтра. */
export interface AdvertiserFacets {
  sources: Partial<Record<AdvertiserSource, number>>;
  addresses: Partial<Record<AddressState, number>>;
  letters: Partial<Record<OfferState, number>>;
  /** Донор → сколько рекламодателей на него ссылается. */
  donors: Record<string, number>;
}

export interface AdvertiserListView {
  rows: AdvertiserCard[];
  total: number;
  page: number;
  /** Размер страницы — называет сервер. */
  limit: number;
  /** Сколько строк выгрузка кладёт в файл за раз. */
  export_limit: number;
}

/** Фильтры — именами адреса экрана и сервера: одна строка параметров у таблицы и файла. */
export interface AdvertiserQuery {
  search?: string;
  donor?: string;
  source?: AdvertiserSource;
  address?: AddressState;
  letter?: OfferState;
}

export function fetchAdvertiserList(
  query: AdvertiserQuery,
  page: number,
): Promise<AdvertiserListView> {
  // Страница — после фильтров; первая — без номера в адресе.
  return request<AdvertiserListView>(
    withQuery('/advertisers/list', page > 1 ? { ...query, page } : query),
  );
}

export function fetchAdvertiserFacets(): Promise<AdvertiserFacets> {
  return request<AdvertiserFacets>('/advertisers/list/facets');
}

/** Те же строки, что в списке под тем же фильтром, — файлом CSV. */
export function exportAdvertisers(query: AdvertiserQuery): Promise<Downloaded> {
  return download(withQuery('/advertisers/list/export', query));
}
