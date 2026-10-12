/**
 * Приёмка Этапа 2 по донору (сверка ТЗ 11.10.2026): «по донору 100 рекламодателей
 * с контактами, письма ушли 70% из них».
 *
 * Типы — здесь, рядом с запросом: их читает только вид «Приёмка по донорам».
 */

import { request } from './client';
import type { CrawlRow } from './crawls';

/** Числа приёмки: найдено, переведено, с адресом, письмо ушло — и пройден ли порог ТЗ. */
export interface Tally {
  found: number;
  /** Из найденных — переведены в рекламодатели, которым пишем. */
  promoted: number;
  /** Из переведённых — с адресом в базе. */
  with_address: number;
  /** Из тех, что с адресом, — кому ушло письмо. */
  sent: number;
  /** Доля ушедших от тех, что с адресом; адресов нет — `null`. */
  sent_share: number | null;
  accepted: boolean;
}

export interface DonorAcceptance {
  host: string;
  /** Последний обход донора — тем же видом, что на панели «Обход доноров». */
  crawl: CrawlRow | null;
  tally: Tally;
  /** Донор-поставщик: его рекламодателей не трогаем, порог ему не задача. */
  supplier: boolean;
}

export interface AcceptanceView {
  donors: DonorAcceptance[];
  /** Сколько обойдённых доноров всего: строк бывает меньше. */
  total: number;
  /** Все доноры вместе, без поставщиков, — по разным доменам. Справочно: критерий ТЗ —
   *  по донору. */
  overall: Tally;
  /** Пороги ТЗ — от сервера, а не копией чисел в экране. */
  target_with_address: number;
  target_sent_share: number;
}

export function fetchAcceptance(): Promise<AcceptanceView> {
  return request<AcceptanceView>('/advertisers/acceptance');
}
