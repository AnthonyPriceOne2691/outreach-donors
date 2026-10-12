/**
 * Перезапрос цены у донора, чья цена старше срока свежести (`letters/price_recheck.py`,
 * 12.10.2026). ТЗ, Этап 2: «свежесть цены 150 дней; старше — сначала перезапрос цены
 * у донора».
 *
 * Можно ли перезапросить, куда уйдёт письмо и почему нельзя — решает сервер и говорит
 * словами: экран их показывает до нажатия и после. Типы — здесь, рядом с запросами:
 * `types.ts` у своего потолка длины.
 */

import { request } from './client';
import type { PriceSource } from './manualPrice';

/** Шаг письма-перезапроса в переписке — как у сервера (`chain.RECHECK_STEP`): лента
 *  переписки называет письмо по шагу. Сверку с сервером держит
 *  `tests/test_price_recheck_rules.py`. */
export const PRICE_RECHECK_STEP = 200;

/** Куда уйдёт перезапрос: ответом в переписку, где донор отвечал, или новым первым
 *  письмом — переписки нет. */
export type RecheckWay = 'thread' | 'first';

/** Донор с протухшей ценой: можно ли перезапросить и куда уйдёт письмо. */
export interface RecheckRow {
  donor_id: number;
  host: string;
  price: number | null;
  currency: string | null;
  source: PriceSource | null;
  priced_at: string | null;
  /** Когда последний перезапрос ушёл донору; `null` — не перезапрашивали. */
  asked_at: string | null;
  /** Почему сейчас не уйдёт — словами сервера; `null` — уйдёт. */
  refusal: string | null;
  way: RecheckWay | null;
  thread_id: number | null;
  /** На какой адрес и с какого ящика. Ящик первого письма выбирает отправка — `null`. */
  email: string | null;
  mailbox: string | null;
}

/** Перезапрос одного донора — с текстом письма целиком: его читают до нажатия. */
export interface RecheckCard extends RecheckRow {
  subject: string | null;
  body: string | null;
}

export interface RechecksView {
  rows: RecheckRow[];
  /** Всего доноров с протухшей ценой: строк бывает меньше — по потолку списка. */
  total: number;
  /** Срок свежести цены, дней. */
  ttl_days: number;
  /** Перезапрос одному донору — не чаще раза в столько дней. */
  every_days: number;
  /** Сколько доноров перезапрашивается одним нажатием — число сервера. */
  batch_max: number;
}

export interface RecheckSent {
  donor_id: number;
  host: string;
  message_id: number;
  thread_id: number | null;
  way: RecheckWay;
  sender_email: string;
  /** Ушло ли письмо на самом деле: у нулевой почты — нет. */
  real: boolean;
}

export interface RecheckRefused {
  donor_id: number;
  host: string | null;
  reason: string;
}

/** Итог нажатия — по каждому донору: что ушло и почему не ушло остальное. */
export interface RecheckResult {
  sent: RecheckSent[];
  refused: RecheckRefused[];
}

/** Прежняя цена донора — для «Цена та же» у ответа на перезапрос без числа. */
export interface SamePrice {
  /** Деньги строкой, как у остальных цен: сервер не теряет копейки. */
  price: string;
  currency: string | null;
}

/** «Цена та же»: прежняя цена легла в карточку временем ответа. */
export interface SamePriceDone {
  reply_id: number;
  price: string;
  currency: string | null;
  at: string;
  /** Легла ли цена в карточку: слово донора новее этого ответа её не пускает. */
  stored: boolean;
}

export function confirmSamePrice(replyId: number): Promise<SamePriceDone> {
  return request<SamePriceDone>(`/price-rechecks/replies/${replyId}/same-price`, {
    method: 'POST',
  });
}

export function fetchStalePrices(): Promise<RechecksView> {
  return request<RechecksView>('/price-rechecks');
}

export function fetchRecheck(donorId: number): Promise<RecheckCard> {
  return request<RecheckCard>(`/price-rechecks/${donorId}`);
}

export function askPrices(donorIds: number[]): Promise<RecheckResult> {
  return request<RecheckResult>('/price-rechecks', {
    method: 'POST',
    body: { donor_ids: donorIds },
  });
}

/** Предложение с точкой в конце: слова сервера бывают и без неё («Донора №7 нет»). */
function sentence(text: string): string {
  return /[.!?…]$/.test(text) ? text : `${text}.`;
}

/** Почему не ушло — словами сервера; домен впереди, если сами слова его не называют. */
function refusedLine(one: RecheckRefused): string {
  const said = sentence(one.reason);
  return one.host !== null && !said.includes(one.host) ? `${one.host}: ${said}` : said;
}

/** Что вышло из нажатия — одной строкой: что ушло и почему не ушли остальные. */
export function recheckSummary(done: RecheckResult): string {
  const parts: string[] = [];
  if (done.sent.length > 0) {
    const hosts = done.sent.map((one) => one.host).join(', ');
    parts.push(
      done.sent.every((one) => one.real)
        ? `Перезапрос цены ушёл: ${hosts}.`
        : `Перезапрос помечен отправленным, но наружу НЕ ушёл — транспорт не настроен: ${hosts}.`,
    );
  }
  parts.push(...done.refused.map(refusedLine));
  return parts.join(' ');
}
