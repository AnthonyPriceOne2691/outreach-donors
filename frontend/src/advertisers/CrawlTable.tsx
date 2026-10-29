/**
 * Состояние обхода словами и значком, цена донора и «Переобойти» — общие у вкладки «Обход
 * доноров» (`CrawlDonorsTable`), журнала обходов (`RecentCrawls`) и «Приёмки по донорам».
 * Вынесены из панели обхода, когда она стала одной строкой в покое (09.10.2026); таблица доноров
 * панели ушла вместе с панелью на вкладку (29.10.2026).
 *
 * **Доля закрытых страниц — у строки обхода** (сверка ТЗ 11.10.2026): «обойдён:
 * 400 страниц» не отличал донора, отдавшего всё, от закрывшего от нас треть
 * запросов, а от этой доли считается смета Этапа 2. Число — с запросами за ним:
 * «закрыто 100%» на трёх запросах и на тысяче — разные новости. Под ним — мера
 * здоровья ТЗ словами ТЗ: «403/429/5xx/таймаут — 4,2% за последние 500 запросов».
 *
 * **Значок — с потолком, под ним — почему обход кончился** (23.10.2026, `crawlWords`): один
 * вид в таблице доноров, последних обходах и «Приёмке по донорам». У сайта, не пустившего с
 * самого начала, — тоже причина, а у ждущего — его место в очереди (29.10.2026).
 */

import { Badge, Button, Group, Stack, Text } from '@mantine/core';
import type { UseMutationResult } from '@tanstack/react-query';

import type { CrawlLaunch, CrawlRow, CrawlTarget } from '../api/crawls';
import { InfoHint } from '../components/InfoHint';
import { formatMoney, formatNumber, formatPercent } from '../format';
import { RunReason } from '../runs/RunReason';
import { pagesOutOf, queueWords, requestsOf, stopWords } from './crawlWords';

const ACTIVE = new Set(['queued', 'running']);

export function isActive(crawl: CrawlRow | null): boolean {
  return crawl !== null && ACTIVE.has(crawl.status);
}

/** Запуск повтора одного донора — общий у таблицы доноров и «Последних обходов». */
export type StartCrawls = UseMutationResult<CrawlLaunch, Error, string[]>;

/** «Переобойти» донора — у законченного обхода с любым исходом (`RecentCrawls`, шапка). Пока
 *  ставится повтор одного, кнопки остальных ждут: запуск один на обе таблицы. */
export function Again({
  host,
  start,
  compact = false,
}: {
  host: string;
  start: StartCrawls;
  /** Ниже ростом — рядом со значком в строке вкладки, а не под ним. */
  compact?: boolean;
}) {
  const mine = start.variables?.includes(host) ?? false;
  return (
    <Button
      size={compact ? 'compact-xs' : 'xs'}
      variant="light"
      className="press"
      aria-label={`Переобойти ${host}`}
      loading={start.isPending && mine}
      disabled={start.isPending && !mine}
      onClick={() => start.mutate([host])}
    >
      Переобойти
    </Button>
  );
}

/** Что с обходом — словами и цветом; законченный — страницы против потолка (`pagesOutOf`). */
export function crawlState(crawl: CrawlRow): { label: string; color: string } {
  const pages = pagesOutOf(crawl);
  if (crawl.status === 'queued') return { label: 'в очереди', color: 'gray' };
  if (crawl.status === 'running') {
    return {
      label: `идёт: ${formatNumber(crawl.pages)} из ${formatNumber(crawl.max_pages)}`,
      color: 'blue',
    };
  }
  if (crawl.status === 'stopped') return { label: 'остановлен — смотрит человек', color: 'red' };
  // robots.txt не дался (отказ, обрыв, сбой) — не «не открылся»: главную мы и не спрашивали,
  // а без прочитанных правил не обходим (`crawl/robots.py`, 29.10.2026).
  if (crawl.stop_reason === 'robots' && crawl.outcome !== 'forbidden') {
    return { label: 'robots.txt не отдал', color: 'red' };
  }
  switch (crawl.outcome) {
    case 'ok':
      return { label: `обойдён: ${pages}`, color: 'green' };
    case 'partial':
      return { label: `частично: ${pages}`, color: 'yellow' };
    case 'forbidden':
      return { label: 'robots.txt запрещает', color: 'gray' };
    case 'blocked':
      return { label: 'закрылся от нас', color: 'red' };
    default:
      return { label: 'не открылся', color: 'red' };
  }
}

/** «закрыто 12,5% из 480 запросов» — или `null`, если доли нет: обход идёт (числа пишет
 *  его конец), не сделал ни одного запроса или записан до замера. Десятая — как в замере
 *  доступности доноров (`okf/crawl-access.md`: 8,3%), а не целые проценты. */
export function closedShare(crawl: CrawlRow): string | null {
  const share = crawl.blocked_share ?? null;
  const requests = crawl.requests ?? null;
  if (share === null || requests === null) return null;
  return `закрыто ${formatPercent(share)} из ${requestsOf(requests)}`;
}

/** Мера здоровья ТЗ — «доля 403/429/5xx/timeout за последние 500 запросов» — на конец
 *  обхода: «403/429/5xx/таймаут — 4,2% за последние 480 запросов». Окно меньше 500,
 *  если столько запросов не набралось. Нет — `null`. */
export function failureShare(crawl: CrawlRow): string | null {
  const share = crawl.failure_share ?? null;
  const window = crawl.window ?? null;
  if (share === null || window === null) return null;
  return `403/429/5xx/таймаут — ${formatPercent(share)} за последние ${requestsOf(window)}`;
}

/** Числа здоровья обхода строками: закрытые страницы и мера ТЗ. */
export function healthLines(crawl: CrawlRow): string[] {
  return [closedShare(crawl), failureShare(crawl)].filter((line) => line !== null);
}

/** Уровни обхода словами — для записки «чего не хватило». */
const LEVELS: Partial<Record<string, string>> = {
  browser: 'браузер',
  proxy: 'прокси',
  render: 'сервис рендеринга',
};

/** Чего каскаду не хватило — по строке на уровень: «Не хватило: прокси — …». Без этого
 *  «все прокси отказали» и выбранный потолок Этапа 2 жили только в базе и журнале. */
export function degradationText(degradation: Record<string, string> | undefined): string | null {
  const lines = Object.entries(degradation ?? {}).map(
    ([level, said]) => `${LEVELS[level] ?? level} — ${said}`,
  );
  return lines.length === 0 ? null : ['Не хватило:', ...lines].join('\n');
}

/** Строки под значком переносятся в ширине колонки: ячейки `fixedTable` в строку не
 *  переносятся, и «кончился час на донора; сайт просит паузу 6 с между страницами»
 *  вылезала из колонки «Последний обход» на 62 px с каждой стороны — на дату цены и число
 *  ссылок; строка меры здоровья — на 28–34 px (замер 23.10.2026, 1440). */
const WRAPS = { whiteSpace: 'normal', textWrap: 'balance' } as const;

/** Состояние обхода значком; под ним — почему законченный обход кончился и числа здоровья.
 *  Вид один везде, где значок стоит: таблица доноров, последние обходы (таблицей с
 *  27.10.2026 — строкой списка числа уезжали в строку с датой) и «Приёмка по донорам». */
export function CrawlState({
  crawl,
  compact = false,
}: {
  crawl: CrawlRow | null;
  /** Строки под значком — в «i» рядом с ним, а не под ним: строка вкладки «Обход доноров» —
   *  в одну высоту (29.10.2026: тысячи доноров простынёй не листаются). */
  compact?: boolean;
}) {
  if (crawl === null) {
    return (
      <Text size="sm" c="dimmed">
        не обходили
      </Text>
    );
  }
  const state = crawlState(crawl);
  // Причина — первой, до чисел здоровья: это ответ на «почему не 1 000»; у ждущего — его место.
  const lines = [stopWords(crawl) ?? queueWords(crawl), ...healthLines(crawl)].filter(
    (said) => said !== null,
  );
  // Причина и «не хватило» — одной запиской за одним значком: два «!» в ячейке читались бы
  // как две разные беды.
  const note = [crawl.reason, degradationText(crawl.degradation)].filter(Boolean).join('\n');
  const line = (
    <Group gap={6} justify="center" wrap="nowrap">
      <Badge variant="light" color={state.color}>
        {state.label}
      </Badge>
      {note ? (
        <RunReason
          reason={note}
          status={crawl.status === 'stopped' ? 'stopped' : 'running'}
          subject="обходов"
        />
      ) : null}
      {compact && lines.length > 0 ? (
        <InfoHint name={`Подробнее: ${state.label}`} width={300}>
          <Stack gap={2}>
            {lines.map((said) => (
              <Text key={said} size="sm">
                {said}
              </Text>
            ))}
          </Stack>
        </InfoHint>
      ) : null}
    </Group>
  );
  if (compact || lines.length === 0) return line;
  // Под значком, а не рядом: значок «обойдён: 1 000 страниц» с долей в одну строку
  // не помещается в колонку «Последний обход».
  return (
    <Stack gap={2} align="center">
      {line}
      {lines.map((said) => (
        <Text key={said} size="xs" c="dimmed" style={WRAPS}>
          {said}
        </Text>
      ))}
    </Stack>
  );
}

/** Цена с валютой, как её назвали; ручная — с пометкой под ней, протухшая — тоже (вкладка
 *  «Обход доноров»: там и доноры с протухшей ценой, а обходят только со свежей). Валюты нет
 *  (цена до 07.10.2026 без неё) — только число: знак доллара был бы догадкой. */
export function PriceCell({ donor }: { donor: CrawlTarget }) {
  if (donor.price === null) return <>—</>;
  const marks = [
    donor.source === 'manual' ? 'вручную' : null,
    donor.price_state === 'stale' ? 'протухла' : null,
  ].filter((mark) => mark !== null);
  return (
    <Stack gap={0} align="center">
      <span>{formatMoney(donor.price, donor.currency)}</span>
      {marks.length > 0 ? (
        <Text size="xs" c={donor.price_state === 'stale' ? 'yellow' : 'dimmed'}>
          {marks.join(' · ')}
        </Text>
      ) : null}
    </Stack>
  );
}

/** Последний обход донора и, если он закончен любым исходом, — «Переобойти» под ним (Anthony
 *  29.10.2026: в «Последних обходах» пять строк, и у остальных доноров повтора было не найти;
 *  галочка и «Обойти отмеченных» ставят повтор тоже, но это неочевидно). Пока обход ждёт или
 *  идёт — кнопки нет: второй незаконченный обход донора сервер не заводит. */
export function LastCrawl({
  donor,
  again,
  compact = false,
}: {
  donor: CrawlTarget;
  again: StartCrawls | null;
  compact?: boolean;
}) {
  const crawl = donor.crawl;
  if (again === null || crawl === null || isActive(crawl)) {
    return <CrawlState crawl={crawl} compact={compact} />;
  }
  // На вкладке — рядом со значком, пока помещается: строка остаётся в одну высоту.
  return compact ? (
    <Group gap={6} justify="center">
      <CrawlState crawl={crawl} compact />
      <Again host={donor.host} start={again} compact />
    </Group>
  ) : (
    <Stack gap={6} align="center">
      <CrawlState crawl={crawl} />
      <Again host={donor.host} start={again} />
    </Stack>
  );
}
