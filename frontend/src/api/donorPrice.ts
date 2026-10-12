/** Цена донора целиком — в строке таблицы, в карточке и в фильтрах (сверка ТЗ 12.10.2026).
 *
 *  ТЗ: «разбирает … белая цена, серая цена, способы оплаты — и кладёт в ту же базу»;
 *  «Доноры — таблица со всеми сайтами … Фильтры, сортировка, выгрузка»; «свежесть цены
 *  150 дней; старше — сначала перезапрос». До этого у строки таблицы не было цены, у
 *  карточки — серой, оплаты, срока и ссылки на ответ.
 *
 *  Отдельным файлом: `types.ts` упёрся в свой потолок. Этот файл `types.ts` читает сам,
 *  поэтому здесь ничего из `types.ts` не берётся — иначе они замкнулись бы в круг. Поля —
 *  необязательные: ответ сервера старше 12.10.2026 их не знает. */

/** Есть ли у донора цена и в сроке ли (`table_filters.PriceState`). */
export type PriceState = 'priced' | 'fresh' | 'stale' | 'none';

/** Решение человека — кто на экране (`table_filters.Review`): без фильтра — доноры. */
export type ReviewScope = 'accepted' | 'rejected' | 'undecided' | 'any';

/** Порядок таблицы (`table_filters.DonorSort`). */
export type DonorSort = 'dr' | 'traffic' | 'price' | 'price_desc' | 'priced_new' | 'priced_old';

/** Поля цены у строки таблицы. */
export interface RowPrice {
  /** Когда цену назвали — от этого дня считается её срок. */
  last_price_at?: string | null;
  /** Цена старше срока — тем же условием, что фильтр «устарела»: сначала перезапрос. */
  price_stale?: boolean;
  /** Ответ донора, снявший цену: «не продаёт» и «бесплатно» стоят на её месте. */
  seller_answer?: 'sells' | 'free' | 'declines' | null;
  /** Решение человека; `null` — не решали. Видно, когда экран — не только доноры. */
  review?: 'accepted' | 'rejected' | null;
}

/** Что донор сказал рядом с ценой — поля карточки. Валюта у белой и серой одна —
 *  `last_price_currency`: у ответа она одна на обе цены. */
export interface PriceDetails {
  last_price_white?: string | null;
  last_price_grey?: string | null;
  last_payment_methods?: string[] | null;
  /** Срок публикации в днях и `dofollow` / `nofollow` — словами донора. */
  last_placement_days?: number | null;
  last_link_type?: string | null;
  /** Ответ, из которого цена, и его переписка: карточка ведёт в неё. */
  last_price_reply_id?: number | null;
  last_price_thread_id?: number | null;
  /** Что донор написал в этом ответе — без цитаты нашего письма и подписи. */
  last_price_reply_text?: string | null;
  /** До какого дня цена свежая — правилом сервера. */
  price_expires_at?: string | null;
}

/** Счётчики вариантов фильтров цены и решения — у страницы таблицы. */
export interface PriceFacets {
  /** Состояние цены → сколько доноров экрана. */
  prices?: Partial<Record<PriceState, number>>;
  /** Решение человека → сколько записей во всей базе. */
  reviews?: Partial<Record<ReviewScope, number>>;
  /** Срок годности цены, дней: «старше N дней» — числом сервера, своей копии нет. */
  price_ttl_days?: number;
}
