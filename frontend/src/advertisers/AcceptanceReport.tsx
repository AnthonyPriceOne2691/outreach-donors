/**
 * «Приёмка по донорам» — порог ТЗ Этапа 2 по каждому обойдённому донору и что нашёл его обход.
 *
 * Сверка ТЗ 11.10.2026: «по донору 100 рекламодателей с контактами, дедуплицировано,
 * письма ушли 70% из них» — этих чисел не было нигде. Строка — донор, пройден ли порог, его
 * последний обход (тем же видом, что на вкладке «Обход доноров», с долей закрытых страниц),
 * сколько у него рекламодателей, сколько из них с адресом и скольким ушло письмо. Чего не
 * хватает — словами: «с адресом 34 из 100».
 *
 * **Что нашёл обход — между обходом и рекламодателями** (тест-группа 23.10.2026; одной колонкой —
 * с 10.11.2026): «куплено», спорные и сколько решено, «мимо», «кому не пишем», страницы со
 * ссылками — числа сервера (`crawl/findings.py`). **Все доноры вместе — первой строкой таблицы**,
 * справочно.
 *
 * **С адресом и «ушло» — среди переведённых** (проверка ветки 12.10.2026): домен, снятый
 * стоп-листом, с адресом от Этапа 1 приёмку не украшает. **Поставщик — помечен**: его
 * рекламодателей не трогаем, порог ему не задача. Пороги — от сервера.
 *
 * **По пятнадцать доноров на странице** (слово Anthony 05.11.2026: «огромная простыня»): считает
 * сервер, номер — в адресе (`?page=2`), порядок прежний — сверху у кого рекламодателей больше.
 * «Все доноры вместе» — первой строкой каждой страницы, по всем донорам, а не по её строкам.
 *
 * **Колонка донора залита, только пока таблица уехала вбок** (`stickyScrolled`, замечание Anthony
 * 10.11.2026: «выделяется чёрным»): заливка нужна, чтобы под донором не читались уехавшие буквы, а в
 * тёмной теме три слоя стекла дали чёрную полосу с острыми углами на таблице, которая никуда не ехала.
 * Без прокрутки колонка прозрачна, как вся карточка; при прокрутке — заливка с круглыми углами и тенью.
 *
 * **Таблица — в карточку на 1440 с открытым меню** (разведка 10.11.2026): одиннадцать колонок на
 * 1 480 px в карточке 1 093 px прятали за краем «С адресом», «Письмо ушло» и «Приёмку» — то, по чему
 * считается порог над таблицей. Теперь семь колонок на 1 070 px: вердикт — второй, сразу за
 * донором; обход — значком с «i», как на вкладке «Обход доноров», а на виду под ним — только
 * отклонение больше нуля (было четыре строки чисел, почти везде нулей, и значок резался
 * многоточием); что нашёл обход — одной колонкой, сеткой «подпись число» (`foundGrid`).
 */

import { Alert, Badge, Card, Loader, Stack, Table, Text } from '@mantine/core';
import { useState } from 'react';
import type { UIEvent } from 'react';

import { fetchAcceptance } from '../api/acceptance';
import type { AcceptanceView, Findings, Tally } from '../api/acceptance';
import { refusalOf } from '../api/client';
import type { CrawlRow } from '../api/crawls';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { HintLines } from '../components/HintLines';
import { PageHead } from '../components/PageHead';
import { PageSwitch, usePageParam, useTurnToTop } from '../components/PageSwitch';
import { Seams } from '../components/Seams';
import { usePagedQuery } from '../components/useUrlFilters';
import { formatNumber, formatPercent } from '../format';
import { CrawlState, deviationLines } from './CrawlTable';

export const ACCEPTANCE_KEY = ['advertisers', 'acceptance'] as const;

/** Как устроен вид — абзацем на колонку: подсказку читают, чтобы понять одно число. */
const ABOUT_LINES = [
  'Числа — по последнему обходу донора; домен у донора — один раз.',
  'Куплено — скоринг уверен, что это купленное размещение, и человек не сказал «не пишем».',
  'Спорных — решает человек; «решено» — сколько он уже решил.',
  'Мимо — не реклама. Не пишем — домен из списка «Кому не пишем»: соцсеть, СМИ, гос. сайт, крупный сайт.',
  'Страниц — страниц донора со ссылкой на его рекламодателей; витрина с пятью — одна.',
  'Рекламодателей — «куплено» и все, кому человек сказал «пишем»: их переводит «Перевести в рекламодатели».',
  'С адресом — из переведённых. Письмо ушло — платформа приняла хоть одно, отказ доставки тоже; доля — от тех, что с адресом.',
  'Поставщик не считается: его рекламодателей не трогаем. «Все доноры вместе» — рекламодатели, адреса и письма по разным доменам, остальное — суммой.',
];

const ABOUT = <HintLines lines={ABOUT_LINES} />;

/** Колонки — по самому длинному содержимому, замер шрифтом экрана 10.11.2026 (поле ячейки — 6 px,
 *  `acceptanceTable`): имя донора переносится по швам (`Seams`); чего не хватает — по «с адресом 99
 *  из 100»; обход — по значку «остановлен — смотрит человек» (`crawlSlot`), «i» и «!» при нём —
 *  строкой ниже; что нашёл обход — по сетке с четырёхзначными числами («не пишем 9 999»);
 *  «Рекламодателей» и «Письмо ушло» — заголовком в две строки (`headsWrap`), по «переведено 9 999»
 *  и «9 999 · 99,9%». */
export const COLUMNS: Column[] = [
  { title: 'Донор', width: '10rem' },
  { title: 'Приёмка', width: '8.75rem' },
  { title: 'Обход', width: '14.125rem' },
  { title: 'Что нашёл обход', width: '13.5rem' },
  { title: 'Рекламо\u00adдателей', width: '7.75rem' },
  { title: 'С адресом', width: '5.75rem' },
  { title: 'Письмо ушло', width: '7rem' },
];
/** Уже — прокрутка таблицы, а донор стоит на месте (`stickyName`): сумма ширин колонок. В карточке
 *  на 1440 с открытым меню — 1 093 px, и таблица встаёт без прокрутки. */
export const TABLE_MIN_WIDTH = 1070;

/** На телефоне строка — карточкой, числа — по три в ряд (`phoneCardsRow`); приёмка, обход и что
 *  нашёл обход — во всю ширину карточки. В таблице сетки нет, и правило ни на что не действует. */
const WHOLE_LINE = { gridColumn: '1 / -1' } as const;

/** Чего не хватает до порога — словами; порог пройден — пусто. */
export function shortOf(tally: Tally, view: AcceptanceView): string[] {
  const missing: string[] = [];
  if (tally.with_address < view.target_with_address) {
    missing.push(
      `с адресом ${formatNumber(tally.with_address)} из ${formatNumber(view.target_with_address)}`,
    );
  }
  const share = tally.sent_share;
  if (share !== null && share < view.target_sent_share) {
    missing.push(`ушло ${formatPercent(share)} из ${formatPercent(view.target_sent_share)}`);
  }
  return missing;
}

/** «1 · 50%»: скольким ушло и какая это доля тех, что с адресом. */
function sentLine(tally: Tally): string {
  const share = tally.sent_share === null ? '' : ` · ${formatPercent(tally.sent_share)}`;
  return `${formatNumber(tally.sent)}${share}`;
}

/** Вердикт и под ним — чего не хватает, по строке на условие: «с адресом 34 из 100», «ушло 58,8%
 *  из 70%». У поставщика — что он не считается; почему — в «i» вида. */
function Verdict({
  tally,
  view,
  supplier,
}: {
  tally: Tally;
  view: AcceptanceView;
  supplier: boolean;
}) {
  if (tally.accepted && !supplier) {
    return (
      <Badge variant="light" color="green">
        пройдена
      </Badge>
    );
  }
  const notes = supplier ? ['не считается'] : shortOf(tally, view);
  return (
    <Stack gap={2} align="center">
      <Badge variant="light" color="gray">
        {supplier ? 'поставщик' : 'не пройдена'}
      </Badge>
      {notes.map((note) => (
        <Text key={note} size="xs" c="dimmed">
          {note}
        </Text>
      ))}
    </Stack>
  );
}

/** Число и под ним — пояснение, если оно есть. */
function WithNote({ value, note }: { value: number; note: string | null }) {
  return (
    <Stack gap={0} align="center">
      <span>{formatNumber(value)}</span>
      {note === null ? null : (
        <Text size="xs" c="dimmed">
          {note}
        </Text>
      )}
    </Stack>
  );
}

/** «куплено 118» — подпись тихо, число — как в соседних колонках; неразрывно. */
function Found({ label, value }: { label: string; value: number }) {
  return (
    <span className="foundItem">
      <Text span size="xs" c="dimmed">
        {label}
      </Text>
      {'\u00a0'}
      <span>{formatNumber(value)}</span>
    </span>
  );
}

/** Что нашёл обход — одной колонкой, сеткой в две: «куплено» и «мимо», спорные и «не пишем»,
 *  «решено» — под спорными, и страницы. «Решено» — у каждого числа спорных, и нулём: «никто ещё не
 *  смотрел» — тоже ответ; спорных нет — места под ним пусто, и страницы стоят на своём. */
function FoundCell({ found }: { found: Findings }) {
  return (
    <Table.Td data-label="Что нашёл обход" style={WHOLE_LINE}>
      <span className="foundGrid">
        <Found label="куплено" value={found.bought} />
        <Found label="мимо" value={found.skipped} />
        <Found label="спорных" value={found.pending} />
        <Found label={'не\u00a0пишем'} value={found.blocked} />
        {found.pending > 0 ? <Found label="решено" value={found.decided} /> : <span />}
        <Found label="страниц" value={found.pages} />
      </span>
    </Table.Td>
  );
}

/** Рекламодатели, адреса и письма. Не переведённые адреса не получат: искать их начнут после
 *  перевода — это сказано у числа найденных. */
function TallyCells({ tally }: { tally: Tally }) {
  return (
    <>
      <Table.Td data-label="Рекламодателей">
        <WithNote
          value={tally.found}
          note={
            tally.promoted < tally.found ? `переведено\u00a0${formatNumber(tally.promoted)}` : null
          }
        />
      </Table.Td>
      <Table.Td data-label="С адресом">{formatNumber(tally.with_address)}</Table.Td>
      <Table.Td data-label="Письмо ушло">{sentLine(tally)}</Table.Td>
    </>
  );
}

/** Обход — значком с «i», как на вкладке «Обход доноров» (`CrawlState compact`): почему кончился и
 *  доли закрытых и отказов — в «i». На виду под значком — только доля больше нуля: по ней решают. */
function CrawlCell({ crawl }: { crawl: CrawlRow | null }) {
  return (
    <Stack gap={2} align="center">
      <div className="crawlSlot crawlSlotBare">
        <CrawlState crawl={crawl} compact />
      </div>
      {deviationLines(crawl).map((line) => (
        <Text key={line} size="xs" c="dimmed">
          {line}
        </Text>
      ))}
    </Stack>
  );
}

/** Все доноры вместе — над донорами, теми же колонками; обхода у суммы нет. */
function Together({ view }: { view: AcceptanceView }) {
  return (
    <Table.Tr>
      <Table.Td>
        <Text fw={600}>Все доноры вместе</Text>
        <Text size="xs" c="dimmed">
          без поставщиков
        </Text>
      </Table.Td>
      <Table.Td data-label="Приёмка" className="wrapCell" style={WHOLE_LINE}>
        <Text size="xs" c="dimmed">
          справочно, не{'\u00a0'}критерий
        </Text>
      </Table.Td>
      <Table.Td style={WHOLE_LINE} />
      <FoundCell found={view.overall_findings} />
      <TallyCells tally={view.overall} />
    </Table.Tr>
  );
}

function Rows({ view, stale }: { view: AcceptanceView; stale: boolean }) {
  // Прокрутка вбок — событие самой области таблицы; оно не всплывает, поэтому ловится на погружении.
  const [scrolled, setScrolled] = useState(false);
  const onScroll = (event: UIEvent<HTMLDivElement>) => {
    const area = event.target as HTMLElement;
    setScrolled(area.scrollLeft > 0);
  };
  return (
    <div className={scrolled ? 'stickyScrolled' : undefined} onScrollCapture={onScroll}>
      <RowsTable view={view} stale={stale} />
    </div>
  );
}

function RowsTable({ view, stale }: { view: AcceptanceView; stale: boolean }) {
  return (
    <DataTable
      columns={COLUMNS}
      minWidth={TABLE_MIN_WIDTH}
      className="stickyName headsWrap acceptanceTable"
      cardRow
      stale={stale}
    >
      <Together view={view} />
      {view.donors.map((row) => (
        <Table.Tr key={row.host}>
          <Table.Td className="cellName">
            <Text fw={500} className="cellName">
              <Seams text={row.host} />
            </Text>
          </Table.Td>
          {/* Вердикт — сразу за донором: по нему и смотрят. Чего не хватает — переносом: в одну
              строку не помещается ни в колонку, ни в карточку телефона. */}
          <Table.Td data-label="Приёмка" className="wrapCell" style={WHOLE_LINE}>
            <Verdict tally={row.tally} view={view} supplier={row.supplier} />
          </Table.Td>
          {/* Значок с «!» и «глубокий» шире колонки — переносом, а не поверх соседней колонки. */}
          <Table.Td data-label="Обход" className="wrapCell crawlCell" style={WHOLE_LINE}>
            <CrawlCell crawl={row.crawl} />
          </Table.Td>
          <FoundCell found={row.findings} />
          <TallyCells tally={row.tally} />
        </Table.Tr>
      ))}
    </DataTable>
  );
}

export function AcceptanceReport() {
  const [page, goToPage] = usePageParam();
  const { query, data, pages } = usePagedQuery(
    [...ACCEPTANCE_KEY, page],
    // Страницы считает общий хук — по `rows`; строки приёмки — доноры.
    () => fetchAcceptance(page).then((view) => ({ ...view, rows: view.donors })),
    page,
    goToPage,
  );
  const { top, turnToTop } = useTurnToTop<HTMLDivElement>(goToPage);
  if (data === undefined) {
    return query.error ? (
      <Alert color="red" title="Приёмка не загрузилась">
        {refusalOf(query.error)}
      </Alert>
    ) : (
      <Loader aria-label="Считаем приёмку" m="md" />
    );
  }
  return (
    <Card className="glassPanel" p="lg" ref={top}>
      <Stack gap="sm">
        <PageHead title="Приёмка по донорам" hint={ABOUT} />
        <Text size="sm">
          Порог ТЗ: у донора {formatNumber(data.target_with_address)} рекламодателей с адресом, и
          письмо ушло {formatPercent(data.target_sent_share)} из них.
        </Text>
        {/* «Нет» — по числу сервера, а не по пустой странице: страница за концом пуста, хотя
            доноры есть, и экран сейчас уйдёт на последнюю. */}
        {data.total === 0 ? (
          <Text size="sm" c="dimmed">
            Обойдённых доноров нет: приёмка считается по обходу, а он — на вкладке «Обход доноров».
          </Text>
        ) : (
          <>
            <Rows view={data} stale={query.isPlaceholderData} />
            <PageSwitch label="Страницы приёмки" page={page} pages={pages} onChange={turnToTop} />
          </>
        )}
      </Stack>
    </Card>
  );
}
