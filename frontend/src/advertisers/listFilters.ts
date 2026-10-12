/**
 * Фильтры списка рекламодателей и страница — в адресе экрана, слова оффера и адреса.
 *
 * **Адрес, а не состояние компонента** — как у доноров (`donors/donorFilters.ts`):
 * обновление вкладки, «назад» и ссылка коллеге показывают тот же список. Адрес — чужой
 * ввод: незнакомое значение — «не сужать», а не отказ сервера и не пустой экран.
 *
 * Имена параметров — те же, что у сервера (`AdvertiserQuery`): одна строка параметров
 * у таблицы, у выгрузки и у «назад». Вид экрана (`view=list`) едет рядом и фильтром
 * не считается.
 */

import type {
  AddressState,
  AdvertiserQuery,
  AdvertiserSource,
  OfferState,
} from '../api/advertiserList';
import type { Emptiness } from '../components/WholeRow';
import { formatNumber } from '../format';

export interface ListFilters {
  /** Подстрока домена. Пусто — не сужать. */
  search: string;
  donor: string | null;
  source: AdvertiserSource | null;
  address: AddressState | null;
  letter: OfferState | null;
  page: number;
}

export const NO_LIST_FILTERS: ListFilters = {
  search: '',
  donor: null,
  source: null,
  address: null,
  letter: null,
  page: 1,
};

/** Что с оффером — словом и цветом: мята — дошло до ответа, бирюза — ждём ответа,
 *  янтарь — нужно внимание, роза — отписка. Слова — те же, что в файле
 *  (`crawl/offer_state.OFFER_TITLES`). */
export const OFFER_STATES: Record<OfferState, { title: string; color: string }> = {
  replied: { title: 'ответил', color: 'green' },
  sent: { title: 'ушло', color: 'lagoon' },
  queued: { title: 'собрано', color: 'gray' },
  bounced: { title: 'не дошло', color: 'yellow' },
  unsubscribed: { title: 'отписался', color: 'red' },
  stopped: { title: 'снято', color: 'gray' },
  none: { title: 'не собрано', color: 'gray' },
};

/** Есть ли куда писать — словом фильтра. В строке без адреса стоит исход поиска. */
export const ADDRESS_STATES: Record<AddressState, string> = {
  found: 'адрес найден',
  form: 'только форма',
  none: 'адреса нет',
};

export const SOURCES: Record<AdvertiserSource, string> = {
  links: 'по ссылке',
  niche: 'бизнесы ниши',
};

export const OFFER_KEYS = Object.keys(OFFER_STATES) as OfferState[];
export const ADDRESS_KEYS = Object.keys(ADDRESS_STATES) as AddressState[];
export const SOURCE_KEYS = Object.keys(SOURCES) as AdvertiserSource[];

function known<T extends string>(raw: string | null, keys: readonly T[]): T | null {
  return keys.find((key) => key === raw) ?? null;
}

function pageOf(raw: string | null): number {
  if (raw === null || !/^\d{1,6}$/.test(raw)) return 1;
  return Math.max(1, Number(raw));
}

export function readListFilters(params: URLSearchParams): ListFilters {
  const donor = (params.get('donor') ?? '').trim().toLowerCase();
  return {
    search: (params.get('search') ?? '').trim(),
    donor: donor === '' ? null : donor,
    source: known(params.get('source'), SOURCE_KEYS),
    address: known(params.get('address'), ADDRESS_KEYS),
    letter: known(params.get('letter'), OFFER_KEYS),
    page: pageOf(params.get('page')),
  };
}

/** Фильтры → запрос к серверу, без страницы. */
export function listQueryOf(filters: ListFilters): AdvertiserQuery {
  return {
    ...(filters.search !== '' ? { search: filters.search } : {}),
    ...(filters.donor !== null ? { donor: filters.donor } : {}),
    ...(filters.source !== null ? { source: filters.source } : {}),
    ...(filters.address !== null ? { address: filters.address } : {}),
    ...(filters.letter !== null ? { letter: filters.letter } : {}),
  };
}

/** Фильтры → адрес экрана: вид списка, условия, страница (первая — без номера). */
export function writeListFilters(filters: ListFilters): URLSearchParams {
  const params = new URLSearchParams({ view: 'list' });
  for (const [key, value] of Object.entries(listQueryOf(filters))) {
    params.set(key, String(value));
  }
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

export function isListFiltered(filters: ListFilters): boolean {
  return Object.keys(listQueryOf(filters)).length > 0;
}

/** Что сужает список — словами: «ничего не нашлось» без условий читается как «нет никого». */
export function listConditions(filters: ListFilters): string[] {
  const said: string[] = [];
  if (filters.search !== '') said.push(`«${filters.search}» в домене`);
  if (filters.donor !== null) said.push(`донор ${filters.donor}`);
  if (filters.source !== null) said.push(SOURCES[filters.source]);
  if (filters.address !== null) said.push(ADDRESS_STATES[filters.address]);
  if (filters.letter !== null) said.push(`письмо «${OFFER_STATES[filters.letter].title}»`);
  return said;
}

/** Почему список пуст — словами: рекламодателей нет вовсе или пусто под условиями. */
export function listEmptiness(filters: ListFilters, all: number): Emptiness {
  const resettable = isListFiltered(filters);
  if (all === 0) {
    return {
      title: 'Рекламодателей пока нет.',
      detail:
        'Рекламодателем домен становится переводом: «куплено» и «пишем» на вкладке «Проверка» — ' +
        'кнопкой «Перевести в рекламодатели», бизнес ниши — после «Пишем».',
      resettable,
    };
  }
  const conditions = listConditions(filters);
  return {
    title: 'Под фильтр ничего не попало.',
    detail:
      conditions.length === 0
        ? `На этой странице пусто. Всего рекламодателей — ${formatNumber(all)}.`
        : `Условия: ${conditions.join(', ')}. Всего рекламодателей — ${formatNumber(all)}.`,
    resettable,
  };
}
