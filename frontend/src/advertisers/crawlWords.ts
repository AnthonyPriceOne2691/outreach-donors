/**
 * Почему обход кончился и сколько это от потолка — словами строки обхода (23.10.2026): «частично:
 * N страниц» не говорило, почему не 1 000. Потолки — числами сервера (`config/crawl.py`), а не
 * копией в экране: числа нет (сервер старше экрана) — нет и слов с ним.
 */

import type { CrawlRow, CrawlSource, CrawlTargets } from '../api/crawls';
import { formatNumber, formatSeconds, plural } from '../format';

/** «1 000 страниц» — родительный: после «из» и «не больше». */
function pagesOf(count: number): string {
  return `${formatNumber(count)} ${plural(count, 'страницы', 'страниц', 'страниц')}`;
}

/** «2 000 запросов» — родительный: после «из», «за последние» и «не больше». */
export function requestsOf(count: number): string {
  return `${formatNumber(count)} ${plural(count, 'запроса', 'запросов', 'запросов')}`;
}

/** Страницы против потолка: «380 из 1 000 страниц»; потолка нет — как было: «380 страниц». */
export function pagesOutOf({ pages, max_pages: ceiling }: CrawlRow): string {
  if (ceiling > 0) return `${formatNumber(pages)} из ${pagesOf(ceiling)}`;
  return `${formatNumber(pages)} ${plural(pages, 'страница', 'страницы', 'страниц')}`;
}

const HOUR = 'час';

/** Срок словами: «час», «2 ч», «1 ч 30 мин», «45 мин», «30 с». */
export function spanWords(seconds: number): string {
  if (seconds < 60) return formatSeconds(Math.round(seconds));
  const minutes = Math.round(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${formatNumber(rest)} мин`;
  const head = hours === 1 && rest === 0 ? HOUR : `${formatNumber(hours)} ч`;
  return rest === 0 ? head : `${head} ${formatNumber(rest)} мин`;
}

/** Адреса кончились — где именно: «больше нет» про карту и про ссылки — разные новости. */
const RUN_OUT: Record<CrawlSource, string> = {
  sitemap: 'в карте сайта больше адресов нет',
  links: 'по ссылкам больше страниц не нашли',
  'sitemap+links': 'в карте сайта и по ссылкам больше адресов нет',
};

function runOutWords(crawl: CrawlRow): string {
  const source = crawl.source ?? null;
  // Записан до отчёта: откуда брались адреса, не знаем, что кончились — знаем.
  if (source === null) return 'адреса для обхода кончились';
  // По неполной карте «больше нет» — не вся правда: в недочитанных файлах могли быть страницы.
  const partial = source !== 'links' && crawl.sitemap_complete === false;
  return partial ? `${RUN_OUT[source]}, но карта прочитана не вся` : RUN_OUT[source];
}

/** Пауза сайта — когда она хотя бы вдвое дольше нашей (при нашей 1 с — от 2 с): короче темп
 *  почти наш. Дольше нашего потолка паузы — и сколько ждём на деле. */
function pauseWords(crawl: CrawlRow): string | null {
  const asked = crawl.crawl_delay ?? null;
  const ours = crawl.delay_sec ?? null;
  if (asked === null || ours === null || asked <= ours || asked < 2 * ours) return null;
  const said = `сайт просит паузу ${formatSeconds(asked)} между страницами`;
  const most = crawl.max_delay_sec ?? null;
  return most !== null && asked > most ? `${said} — ждём ${formatSeconds(most)}` : said;
}

/** Время кончилось: свой срок обхода, если он его записал, иначе общий потолок. */
function timeoutWords(crawl: CrawlRow): string {
  const budget = crawl.time_budget_sec ?? crawl.max_seconds ?? null;
  const span = budget === null ? null : spanWords(budget);
  let spent = 'кончилось время на донора';
  if (span === HOUR) spent = 'кончился час на донора';
  else if (span !== null) spent = `кончилось время на донора — ${span}`;
  const pause = pauseWords(crawl);
  return pause === null ? spent : `${spent}; ${pause}`;
}

/** Почему законченный обход кончился — коротко. `null` — у идущего и остановленного разбором
 *  (причина за «!»); у robots.txt и неоткрывшейся главной исход значка говорит сам. */
export function stopWords(crawl: CrawlRow): string | null {
  if (crawl.status !== 'done') return null;
  switch (crawl.stop_reason) {
    case 'exhausted':
      return runOutWords(crawl);
    case 'max_pages':
      return crawl.max_pages > 0 ? `потолок ${pagesOf(crawl.max_pages)} на донора` : null;
    case 'max_attempts':
      return crawl.max_attempts === undefined
        ? 'кончились попытки'
        : `кончились попытки — ${requestsOf(crawl.max_attempts)}`;
    case 'timeout':
      return timeoutWords(crawl);
    case 'unhealthy':
      return 'сайт стал отказывать — остановились';
    default:
      return null;
  }
}

/** Потолки на донора и пауза одной фразой: «не больше 1 000 страниц, 2 000 запросов и часа;
 *  пауза … не меньше 1 с и не больше 10 с». Чисел нет — фразы нет. */
function limitsWords(data: CrawlTargets): string | null {
  const { max_attempts: attempts, max_seconds: seconds, delay_sec: pause } = data;
  const most = data.max_delay_sec;
  if (attempts === undefined || seconds === undefined) return null;
  if (pause === undefined || most === undefined) return null;
  const span = spanWords(seconds);
  return (
    ` На донора — не больше ${pagesOf(data.max_pages)}, ${requestsOf(attempts)} и ` +
    `${span === HOUR ? 'часа' : span}${slowWords(data, seconds)}; пауза между страницами — ` +
    `та, что просит сайт в robots.txt, но не меньше ${formatSeconds(pause)} и не больше ` +
    `${formatSeconds(most)}. Что кончилось первым — под значком обхода.`
  );
}

/** Срок сайту с долгой паузой — если сервер его даёт: « (… — до 3 ч, чтобы успеть страницы)». */
function slowWords(data: CrawlTargets, seconds: number): string {
  const slow = data.max_seconds_slow ?? null;
  if (slow === null || slow <= seconds) return '';
  return ` (сайту с долгой паузой — до ${spanWords(slow)}, чтобы успеть страницы)`;
}

/** Как идёт обход и где он кончается — для «i» панели, числами сервера. Ответа ещё нет —
 *  только путь обхода: чисел, которых не прислал сервер, экран не выдумывает. */
export function crawlRule(data: CrawlTargets | undefined): string {
  const limits = data === undefined ? null : limitsWords(data);
  return (
    ' Обход — с главной по карте сайта, свежие адреса первыми; карта кончилась или её нет — дальше по ссылкам сайта.' +
    (limits ?? '')
  );
}
