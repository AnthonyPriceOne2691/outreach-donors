/**
 * Ночной обход — строкой на вкладке «Обход доноров» (`crawl/night.py`, 02.11.2026).
 *
 * Круг идёт сам, ночью, и видеть его можно только здесь: когда был, сколько доноров, что считали,
 * что осталось. Выключен — так и сказано, а не пусто: иначе тишина читалась бы «ночью ничего не
 * понадобилось». Расчёт — в «i»: строка говорит о деле, а из чего вышло число, нужно не каждый день.
 *
 * **«Каталогов отложено: N» — рядом, со своим «i»** (12.11.2026). Доноров-каталогов круг не обходит
 * с 11.11.2026 (`crawl/targets.py`, `skip_catalogs`), и на экране этого не было видно: донор,
 * которого ночь не тронула, читался забытым. Ноль не показывается.
 */

import { Group, Stack, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import type { CrawlNightRow, CrawlNightView } from '../api/crawls';
import { fetchCrawlNight } from '../api/crawls';
import { InfoHint } from '../components/InfoHint';
import { formatNumber, plural } from '../format';
import { spanWords } from './crawlWords';

export const NIGHT_KEY = ['crawl-night'] as const;

/** Как часто спрашивать: круг идёт часами, и обходы идут десятками минут. */
const POLL_MS = 60_000;

const MOSCOW: Intl.DateTimeFormatOptions = { timeZone: 'Europe/Moscow' };

/** День круга: «03.11». Дата приходит днём, а не моментом: часовой пояс её не сдвигает. */
export function nightDay(night: string): string {
  const [, month, day] = night.split('-');
  return `${day}.${month}`;
}

/** Часы по Москве: «05:30». Часы круга — московские, как и его полночь. */
export function nightClock(moment: string): string {
  return new Date(moment).toLocaleTimeString('ru-RU', {
    ...MOSCOW,
    hour: '2-digit',
    minute: '2-digit',
  });
}

function donors(count: number): string {
  return `${formatNumber(count)} ${plural(count, 'донор', 'донора', 'доноров')}`;
}

/** Строка о круге, который идёт: сколько отдано, сколько ждёт, до какого часа. */
function runningWords(row: CrawlNightRow): string {
  const until = row.issue_until === null ? '' : `, новые — до ${nightClock(row.issue_until)}`;
  return (
    `Ночной обход идёт: отдано обходчикам ${formatNumber(row.given)} из ` +
    `${formatNumber(row.taken)}, ждут ${formatNumber(row.waiting)}${until}.`
  );
}

/** Строка о законченном круге: сколько пошло, что снято и что ждёт следующей ночи. */
function doneWords(row: CrawlNightRow): string {
  if (row.taken === 0) return `Ночной обход ${nightDay(row.night)}: ${emptyWords(row)}.`;
  const dropped = row.dropped > 0 ? `, снято по сроку ${formatNumber(row.dropped)}` : '';
  return (
    `Ночной обход ${nightDay(row.night)}: пошло ${formatNumber(row.given)} из ` +
    `${donors(row.taken)}${dropped}${leftWords(row)}.`
  );
}

/** Почему в круге никого: обходить некого — не то же, что не хватило мощности. */
function emptyWords(row: CrawlNightRow): string {
  if ((row.donors ?? 0) === 0) return 'обходить было некого';
  return 'мощности не хватило ни на одного донора — смотрите расчёт';
}

/** Сколько не поместилось: «не поместились» без числа не значит ничего. */
function leftWords(row: CrawlNightRow): string {
  const left = row.left_out ?? 0;
  if (left === 0) return '';
  return `; не ${plural(left, 'поместился', 'поместились', 'поместились')} ${donors(left)} — следующими ночами`;
}

/** Строка вкладки по состоянию круга. */
export function nightWords(view: CrawlNightView): string {
  const row = view.last;
  if (!view.enabled && (row === null || row.status !== 'running')) {
    return 'Ночной обход выключен: доноры обходятся кнопками.';
  }
  if (row === null) {
    return `Ночной обход начнётся в полночь по Москве и уложится в ${spanWords(view.window_sec)}.`;
  }
  if (row.status === 'running') return runningWords(row);
  if (row.status === 'done') return doneWords(row);
  const said = row.status === 'missed' ? 'пропущен' : 'не начался';
  return `Ночной обход ${nightDay(row.night)} ${said}: ${row.note ?? 'причины нет'}.`;
}

/** Сколько доноров в каких очередях круга. */
function ranksLine(ranks: Record<string, number>): string {
  const count = (rank: string) => formatNumber(ranks[rank] ?? 0);
  return (
    `Дошли до потолка: ${count('limit')}; не начались или ещё не обходили: ` +
    `${count('no_start')}; обойдены не до потолка: ${count('partial')}.`
  );
}

/** Отложенные после глубокого обхода — строкой, если они есть. */
function restedLines(rested: number): string[] {
  if (rested === 0) return [];
  return [
    `Обойдены глубоко недавно и отложены: ${formatNumber(rested)}: обычный обход заменил бы ` +
      'найденное ими.',
  ];
}

/** Из чего вышло — строками «i». Расчёта нет — `null`, «i» нет. */
export function nightFacts(row: CrawlNightRow | null): string[] | null {
  if (row === null || row.workers === null || row.typical_sec === null) return null;
  return [
    `Обходчиков ${formatNumber(row.workers)}, свободно под круг ${formatNumber(row.slots ?? 0)}.`,
    `Обход донора обычно ${spanWords(row.typical_sec)}, долгий — ${spanWords(row.longest_sec ?? 0)}.`,
    `Круг рассчитан на ${spanWords(row.estimate_sec ?? 0)}.`,
    ranksLine(row.ranks),
    ...restedLines(row.rested ?? 0),
  ];
}

/** Сколько доноров-каталогов отложил круг, о котором говорит строка. Ноль — сказать нечего: круга
 *  не было, он выключен и не идёт или считался до 11.11.2026, когда каталоги ещё обходил. */
export function nightCatalogs(view: CrawlNightView): number {
  const row = view.last;
  if (row === null || (!view.enabled && row.status !== 'running')) return 0;
  return row.catalogs ?? 0;
}

/** Почему каталоги отложены — в «i» у числа: подсказкой, до 75 знаков. */
export const CATALOGS_WHY =
  'Карточки каталога — не рекламодатели. Обойти такого донора можно кнопкой.';

export function CrawlNight() {
  const night = useQuery({
    queryKey: NIGHT_KEY,
    queryFn: fetchCrawlNight,
    refetchInterval: POLL_MS,
  });
  const view = night.data;
  if (view === undefined) return null;
  const facts = nightFacts(view.last);
  const catalogs = nightCatalogs(view);
  return (
    // Строка и число каталогов переносятся целыми: на телефоне число встаёт под строкой.
    <Group gap={6} align="center">
      <Group gap={6} align="center" wrap="nowrap">
        <Text size="sm" c="dimmed">
          {nightWords(view)}
        </Text>
        {facts !== null ? (
          <InfoHint name="Из чего вышел расчёт ночного обхода" width={340}>
            <Stack gap={2}>
              {facts.map((line) => (
                <Text key={line} size="sm">
                  {line}
                </Text>
              ))}
            </Stack>
          </InfoHint>
        ) : null}
      </Group>
      {catalogs > 0 ? (
        <Group gap={6} align="center" wrap="nowrap">
          <Text size="sm" c="dimmed">
            · каталогов отложено:{'\u00a0'}
            {formatNumber(catalogs)}
          </Text>
          <InfoHint name="Почему каталоги отложены" width={260}>
            {CATALOGS_WHY}
          </InfoHint>
        </Group>
      ) : null}
    </Group>
  );
}
