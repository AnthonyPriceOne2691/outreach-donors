/**
 * Условия вкладки «Обход доноров», порядок, размер и номер страницы — в адресе экрана.
 *
 * **Адрес, а не состояние компонента** — по общему правилу списков (`components/useUrlFilters.ts`):
 * обновление вкладки, «назад» и ссылка коллеге показывают тот же список. Адрес — чужой ввод:
 * незнакомое значение — «не сужать», а не отказ сервера. Имена — те же, что у сервера
 * (`api/crawls/donor_schemas.DonorFilter`); вид экрана (`view=crawl`) едет рядом и фильтром не
 * считается. Порядок и размер страницы — не сужение: «найдено N из M» их не считает, а в адрес
 * они пишутся, только когда не умолчание.
 */

import type {
  CrawlCounts,
  CrawlDonorQuery,
  DonorCrawlSort,
  DonorCrawlStatus,
  DonorPriceState,
} from '../api/crawls';
import type { PriceSource } from '../api/manualPrice';
import type { Emptiness } from '../components/WholeRow';
import { formatNumber } from '../format';

export interface CrawlFilters {
  status: DonorCrawlStatus | null;
  price: DonorPriceState | null;
  source: PriceSource | null;
  /** Подстрока домена. Пусто — не сужать. */
  search: string;
  sort: DonorCrawlSort;
  size: number;
  page: number;
}

/** Размеры страницы на выбор; умолчание — середина. */
export const SIZES = [25, 50, 100] as const;
export const DEFAULT_SIZE = 50;

export const NO_CRAWL_FILTERS: CrawlFilters = {
  status: null,
  price: null,
  source: null,
  search: '',
  sort: 'priced',
  size: DEFAULT_SIZE,
  page: 1,
};

/** Статус последнего обхода — словом и цветом, теми же, что у значка обхода (`crawlState`). */
export const CRAWL_STATUSES: Record<DonorCrawlStatus, { title: string; color: string }> = {
  never: { title: 'не обходили', color: 'gray' },
  queued: { title: 'в очереди', color: 'gray' },
  running: { title: 'идёт', color: 'blue' },
  done: { title: 'обойдён', color: 'green' },
  partial: { title: 'частично', color: 'yellow' },
  refused: { title: 'не пустил', color: 'red' },
  stopped: { title: 'остановлен', color: 'red' },
};

export const PRICE_STATES: Record<DonorPriceState, string> = {
  fresh: 'цена в сроке',
  stale: 'цена протухла',
  none: 'без цены',
};

export const PRICE_SOURCES: Record<PriceSource, string> = {
  reply: 'из ответа донора',
  manual: 'вручную',
};

export const CRAWL_SORTS: Record<DonorCrawlSort, string> = {
  priced: 'свежая цена сверху',
  crawled: 'давно не обходили',
  host: 'по домену',
  price: 'дороже сверху',
  links: 'больше ссылок',
};

export const STATUS_KEYS = Object.keys(CRAWL_STATUSES) as DonorCrawlStatus[];
export const PRICE_KEYS = Object.keys(PRICE_STATES) as DonorPriceState[];
export const SOURCE_KEYS = Object.keys(PRICE_SOURCES) as PriceSource[];
export const SORT_KEYS = Object.keys(CRAWL_SORTS) as DonorCrawlSort[];

function known<T extends string>(raw: string | null, keys: readonly T[]): T | null {
  return keys.find((key) => key === raw) ?? null;
}

function pageOf(raw: string | null): number {
  if (raw === null || !/^\d{1,6}$/.test(raw)) return 1;
  return Math.max(1, Number(raw));
}

function sizeOf(raw: string | null): number {
  return SIZES.find((size) => String(size) === raw) ?? DEFAULT_SIZE;
}

export function readCrawlFilters(params: URLSearchParams): CrawlFilters {
  return {
    status: known(params.get('status'), STATUS_KEYS),
    price: known(params.get('price'), PRICE_KEYS),
    source: known(params.get('source'), SOURCE_KEYS),
    search: (params.get('search') ?? '').trim(),
    sort: known(params.get('sort'), SORT_KEYS) ?? 'priced',
    size: sizeOf(params.get('size')),
    page: pageOf(params.get('page')),
  };
}

/** Условия → запрос к серверу: без страницы и размера, без умолчаний. */
export function crawlQueryOf(filters: CrawlFilters): CrawlDonorQuery {
  return {
    ...(filters.status !== null ? { status: filters.status } : {}),
    ...(filters.price !== null ? { price: filters.price } : {}),
    ...(filters.source !== null ? { source: filters.source } : {}),
    ...(filters.search !== '' ? { search: filters.search } : {}),
    ...(filters.sort !== 'priced' ? { sort: filters.sort } : {}),
  };
}

/** Условия → адрес экрана: вид вкладки, условия, размер и страница (первая — без номера). */
export function writeCrawlFilters(filters: CrawlFilters): URLSearchParams {
  const params = new URLSearchParams({ view: 'crawl' });
  for (const [key, value] of Object.entries(crawlQueryOf(filters))) {
    params.set(key, String(value));
  }
  if (filters.size !== DEFAULT_SIZE) params.set('size', String(filters.size));
  if (filters.page > 1) params.set('page', String(filters.page));
  return params;
}

/** Сужено ли: порядок и размер страницы — не сужение. */
export function isCrawlFiltered(filters: CrawlFilters): boolean {
  return (
    filters.status !== null ||
    filters.price !== null ||
    filters.source !== null ||
    filters.search !== ''
  );
}

/** Что сужает список — словами: «ничего не нашлось» без условий читается как «нет никого». */
export function crawlConditions(filters: CrawlFilters): string[] {
  const said: string[] = [];
  if (filters.status !== null) said.push(CRAWL_STATUSES[filters.status].title);
  if (filters.price !== null) said.push(PRICE_STATES[filters.price]);
  if (filters.source !== null) said.push(`цена ${PRICE_SOURCES[filters.source]}`);
  if (filters.search !== '') said.push(`«${filters.search}» в домене`);
  return said;
}

/** Сколько доноров в счётчиках по статусу: у каждого донора статус один. */
export function statusTotal(counts: CrawlCounts): number {
  return STATUS_KEYS.reduce((sum, key) => sum + counts.status[key], 0);
}

/** Почему список пуст — словами: доноров нет вовсе или пусто под условиями. `all` — доноров
 *  вкладки без условий (сводка), а не под ними: иначе пусто под фильтром читалось бы «некого». */
export function crawlEmptiness(filters: CrawlFilters, all: number): Emptiness {
  const resettable = isCrawlFiltered(filters);
  if (all === 0) {
    return {
      title: 'Обходить пока некого.',
      detail:
        'Во вкладке — принятые доноры: сначала прогон Этапа 1 и решение по его кандидатам — или ' +
        'донор, заведённый вручную с ценой, которую агентство знает само.',
      resettable,
    };
  }
  const conditions = crawlConditions(filters);
  return {
    title: 'Под фильтр никто не попал.',
    detail:
      conditions.length === 0
        ? `На этой странице пусто. Всего доноров — ${formatNumber(all)}.`
        : `Условия: ${conditions.join(', ')}. Всего доноров — ${formatNumber(all)}.`,
    resettable,
  };
}

/** Сколько займёт обход `count` доноров при `workers` обходчиках — «≈ 3 ч», «≈ 2 сут»;
 *  обходчиков не спросили или нет — без оценки. */
export function crawlEta(count: number, typicalSec: number, workers: number | null): string | null {
  if (workers === null || workers <= 0 || count <= 0) return null;
  const hours = (Math.ceil(count / workers) * typicalSec) / 3600;
  if (hours < 1) return `≈ ${formatNumber(Math.max(1, Math.round(hours * 60)))} мин`;
  if (hours < 48) return `≈ ${formatNumber(Math.round(hours))} ч`;
  return `≈ ${formatNumber(Math.round(hours / 24))} сут`;
}
