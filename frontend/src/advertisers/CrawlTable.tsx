/**
 * Таблица доноров для обхода и слова о состоянии обхода — у панели «Обход доноров»
 * (`CrawlPanel`). Вынесены, когда панель стала одной строкой в покое (09.10.2026).
 *
 * **Доля закрытых страниц — у строки обхода** (сверка ТЗ 11.10.2026): «обойдён:
 * 400 страниц» не отличал донора, отдавшего всё, от закрывшего от нас треть
 * запросов, а от этой доли считается смета Этапа 2. Число — с запросами за ним:
 * «закрыто 100%» на трёх запросах и на тысяче — разные новости. Под ним — мера
 * здоровья ТЗ словами ТЗ: «403/429/5xx/таймаут — 4,2% за последние 500 запросов».
 *
 * **Значок — с потолком, под ним — почему обход кончился** (23.10.2026, `crawlWords`): один
 * вид в таблице доноров, последних обходах и «Приёмке по донорам».
 */

import { Badge, Checkbox, Group, Stack, Table, Text } from '@mantine/core';

import type { CrawlRow, CrawlTarget } from '../api/crawls';
import type { Column } from '../components/ColumnsHead';
import { ColumnsHead } from '../components/ColumnsHead';
import { Seams } from '../components/Seams';
import { formatDate, formatMoney, formatNumber, formatPercent } from '../format';
import { RunReason } from '../runs/RunReason';
import { pagesOutOf, requestsOf, stopWords } from './crawlWords';

const ACTIVE = new Set(['queued', 'running']);

export function isActive(crawl: CrawlRow | null): boolean {
  return crawl !== null && ACTIVE.has(crawl.status);
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
export function CrawlState({ crawl }: { crawl: CrawlRow | null }) {
  if (crawl === null) {
    return (
      <Text size="sm" c="dimmed">
        не обходили
      </Text>
    );
  }
  const state = crawlState(crawl);
  // Причина — первой, до чисел здоровья: это ответ на «почему не 1 000».
  const lines = [stopWords(crawl), ...healthLines(crawl)].filter((said) => said !== null);
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
    </Group>
  );
  if (lines.length === 0) return line;
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

/** «Цена» — по самой длинной: до 100 000 с копейками и валюта кодом,
 *  «99 999,99 USDT». До 07.10.2026 колонка была на 7rem — под доллары знаком. */
const COLUMNS: Column[] = [
  { title: 'Донор' },
  { title: 'Цена', width: '8.5rem' },
  { title: 'Цена от', width: '8rem' },
  { title: 'Последний обход', width: '17rem' },
  { title: 'Ссылок', width: '7rem' },
];
const PICK_WIDTH = '3.25rem';
/** Уже — прокрутка. Колонки с шириной — 700 px вместе с отметкой; донору
 *  остаётся 176 px, как «Донору» в очереди рекламодателей ниже. До 06.10.2026
 *  здесь было 760, и на телефоне донору доставалось 84 px: домен в строку
 *  не помещался и наезжал на цену. */
const TABLE_MIN_WIDTH = 876;

/** Цена с валютой, как её назвали; ручная — с пометкой под ней. Валюты нет
 *  (цена до 07.10.2026 без неё) — только число: знак доллара был бы догадкой. */
function PriceCell({ donor }: { donor: CrawlTarget }) {
  if (donor.price === null) return <>—</>;
  return (
    <Stack gap={0} align="center">
      <span>{formatMoney(donor.price, donor.currency)}</span>
      {donor.source === 'manual' ? (
        <Text size="xs" c="dimmed">
          вручную
        </Text>
      ) : null}
    </Stack>
  );
}

export function TargetsTable({
  donors,
  picked,
  onPick,
  mayStart,
}: {
  donors: CrawlTarget[];
  picked: Set<string>;
  onPick: (host: string, on: boolean) => void;
  mayStart: boolean;
}) {
  return (
    <Table.ScrollContainer minWidth={TABLE_MIN_WIDTH} type="native" className="scrollSlim">
      {/* Имя донора — слева, как имя в любой таблице, хотя колонка вторая:
          первая — отметка (`pickFirst`). */}
      <Table
        className={mayStart ? 'dataTable fixedTable pickFirst' : 'dataTable fixedTable'}
        layout="fixed"
        tabularNums
        verticalSpacing="sm"
        aria-label="Доноры для обхода"
      >
        <ColumnsHead columns={COLUMNS} lead={mayStart ? PICK_WIDTH : null} />
        <Table.Tbody>
          {donors.map((donor) => (
            <Table.Tr key={donor.host}>
              {mayStart ? (
                <Table.Td>
                  <Group justify="center">
                    <Checkbox
                      aria-label={`Обойти ${donor.host}`}
                      checked={picked.has(donor.host)}
                      disabled={isActive(donor.crawl)}
                      onChange={(event) => onPick(donor.host, event.currentTarget.checked)}
                    />
                  </Group>
                </Table.Td>
              ) : null}
              {/* Вторая колонка в строку не переносится (`fixedTable`), и домен
                  длиннее колонки наезжал на соседнюю — имени перенос нужен,
                  как у «Донора» в очереди ниже: по швам, а не где кончилось
                  место. */}
              <Table.Td className="wrapCell cellName">
                <Seams text={donor.host} />
              </Table.Td>
              <Table.Td>
                <PriceCell donor={donor} />
              </Table.Td>
              <Table.Td>{formatDate(donor.priced_at)}</Table.Td>
              <Table.Td>
                <CrawlState crawl={donor.crawl} />
              </Table.Td>
              <Table.Td>
                {donor.crawl?.links === null || donor.crawl === null
                  ? '—'
                  : formatNumber(donor.crawl.links)}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
