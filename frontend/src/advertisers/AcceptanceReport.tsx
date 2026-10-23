/**
 * «Приёмка по донорам» — порог ТЗ Этапа 2 по каждому обойдённому донору и что нашёл его обход.
 *
 * Сверка ТЗ 11.10.2026: «по донору 100 рекламодателей с контактами, дедуплицировано,
 * письма ушли 70% из них» — этих чисел не было нигде. Строка — донор, его последний
 * обход (тем же видом, что на панели «Обход доноров», с долей закрытых страниц),
 * сколько у него рекламодателей, сколько из них с адресом, скольким ушло письмо
 * и пройден ли порог. Чего не хватает — словами: «с адресом 34 из 100».
 *
 * **Что нашёл обход — колонками между обходом и рекламодателями** (тест-группа 23.10.2026:
 * «нет понимания, сколько с каждого сайта: сколько продажных ссылок, статей, сколько
 * спорных»): «куплено», спорные и под числом — сколько из них решено, «мимо», «кому не
 * пишем», страницы со ссылками на рекламодателей. Числа — сервера (`crawl/findings.py`),
 * экран их не досчитывает. Рекламодателей — «куплено» и все, кому человек сказал «пишем»:
 * их не меньше, чем «куплено». Что значит каждая колонка — в «i» у заголовка.
 *
 * **Все доноры вместе — первой строкой таблицы, справочно**: те же колонки, без поставщиков;
 * рекламодатели, адреса и письма — по разным доменам, найденное обходом — суммой по донорам.
 * Критерий ТЗ — по донору, а сумма отвечает на оговорку самого ТЗ («Арифметика», п. 6).
 * До 23.10.2026 это была строка текста над таблицей — без колонок, которые есть у донора.
 *
 * **С адресом и «ушло» — среди переведённых** (проверка ветки 12.10.2026): домен, снятый
 * стоп-листом, с адресом от Этапа 1 приёмку не украшает. **Поставщик — помечен**: его
 * рекламодателей не трогаем, порог ему не задача. Доноров больше, чем строк, —
 * «показано N из M». Пороги — от сервера.
 */

import { Alert, Badge, Card, Loader, Stack, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { fetchAcceptance } from '../api/acceptance';
import type { AcceptanceView, Findings, Tally } from '../api/acceptance';
import { refusalOf } from '../api/client';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { PageHead } from '../components/PageHead';
import { Seams } from '../components/Seams';
import { formatNumber, formatPercent } from '../format';
import { CrawlState } from './CrawlTable';

export const ACCEPTANCE_KEY = ['advertisers', 'acceptance'] as const;

/** Как устроен вид — абзацем на колонку: подсказку читают, чтобы понять одно число. */
const ABOUT_LINES = [
  'Числа — по последнему обходу донора; домен у донора — один раз.',
  'Куплено — скоринг уверен, что это купленное размещение, и человек не сказал «не пишем».',
  'Спорных — решает человек; «решено» — сколько он уже решил.',
  'Мимо — не реклама. Кому не пишем — домен из списка: соцсеть, СМИ, гос. сайт, крупный сайт.',
  'Страниц — страниц донора со ссылкой на его рекламодателей; витрина с пятью — одна.',
  'Рекламодателей — «куплено» и все, кому человек сказал «пишем»: их переводит «Перевести в рекламодатели».',
  'С адресом — из переведённых. Письмо ушло — платформа приняла хоть одно, отказ доставки тоже; доля — от тех, что с адресом.',
  'Поставщик не считается: его рекламодателей не трогаем. «Все доноры вместе» — рекламодатели, адреса и письма по разным доменам, остальное — суммой.',
];

const ABOUT = (
  <Stack gap={6}>
    {ABOUT_LINES.map((line) => (
      <span key={line}>{line}</span>
    ))}
  </Stack>
);

/** Колонки: донору — остаток; обходу — как «Последний обход» панели обхода (17rem); находкам
 *  обхода — по заголовку и «100 000» строки «вместе», спорным — по «решено 10 000» (замер
 *  шрифтом экрана 23.10.2026: заголовки 73–142 px, «решено 10 000» — 121 px с полями ячейки);
 *  остальным числам — по «1 000 · 100%»; приёмке — по «ушло 69,9% из 70%», длиннее — переносом. */
const COLUMNS: Column[] = [
  { title: 'Донор' },
  { title: 'Обход', width: '17rem' },
  { title: 'Куплено', width: '6rem' },
  { title: 'Спорных', width: '7.75rem' },
  { title: 'Мимо', width: '5.5rem' },
  { title: 'Кому не пишем', width: '9rem' },
  { title: 'Страниц', width: '6rem' },
  { title: 'Рекламодателей', width: '9.5rem' },
  { title: 'С адресом', width: '8rem' },
  { title: 'Письмо ушло', width: '8.5rem' },
  { title: 'Приёмка', width: '12rem' },
];
/** Уже — прокрутка: сумма ширин колонок (1 428 px) и 220 px донору. */
const TABLE_MIN_WIDTH = 1648;

/** На телефоне строка — карточкой, числа — по три в ряд (`phoneCardsRow`); обход и приёмка —
 *  во всю ширину карточки. В таблице сетки нет, и правило ни на что не действует. */
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

function Verdict({
  tally,
  view,
  supplier,
}: {
  tally: Tally;
  view: AcceptanceView;
  supplier: boolean;
}) {
  if (supplier) {
    return (
      <Stack gap={2} align="center">
        <Badge variant="light" color="gray">
          поставщик
        </Badge>
        <Text size="xs" c="dimmed">
          его рекламодателей не трогаем — не считается
        </Text>
      </Stack>
    );
  }
  if (tally.accepted) {
    return (
      <Badge variant="light" color="green">
        пройдена
      </Badge>
    );
  }
  return (
    <Stack gap={2} align="center">
      <Badge variant="light" color="gray">
        не пройдена
      </Badge>
      <Text size="xs" c="dimmed">
        {shortOf(tally, view).join('; ')}
      </Text>
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

/** Что нашёл обход: «куплено», спорные и сколько из них решено, «мимо», «кому не пишем», страницы.
 *  «Решено» — у каждого числа спорных, и нулём: «никто ещё не смотрел» — тоже ответ. */
function FoundCells({ found }: { found: Findings }) {
  return (
    <>
      <Table.Td data-label="Куплено">{formatNumber(found.bought)}</Table.Td>
      <Table.Td data-label="Спорных">
        <WithNote
          value={found.pending}
          note={found.pending > 0 ? `решено\u00a0${formatNumber(found.decided)}` : null}
        />
      </Table.Td>
      <Table.Td data-label="Мимо">{formatNumber(found.skipped)}</Table.Td>
      <Table.Td data-label="Кому не пишем">{formatNumber(found.blocked)}</Table.Td>
      <Table.Td data-label="Страниц">{formatNumber(found.pages)}</Table.Td>
    </>
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

/** Все доноры вместе — над донорами, теми же колонками; имя — на месте донора и обхода. */
function Together({ view }: { view: AcceptanceView }) {
  return (
    <Table.Tr>
      <Table.Td colSpan={2}>
        <Text fw={600}>Все доноры вместе</Text>
        <Text size="xs" c="dimmed">
          без поставщиков
        </Text>
      </Table.Td>
      <FoundCells found={view.overall_findings} />
      <TallyCells tally={view.overall} />
      <Table.Td data-label="Приёмка" className="wrapCell" style={WHOLE_LINE}>
        <Text size="xs" c="dimmed">
          справочно, не критерий
        </Text>
      </Table.Td>
    </Table.Tr>
  );
}

/** «Показано 200 из 315» — когда доноров больше, чем строк. */
function Shown({ view }: { view: AcceptanceView }) {
  if (view.total <= view.donors.length) return null;
  return (
    <Text size="sm" c="dimmed">
      Показано {formatNumber(view.donors.length)} из {formatNumber(view.total)}: сверху — у кого
      рекламодателей больше.
    </Text>
  );
}

function Rows({ view }: { view: AcceptanceView }) {
  return (
    <DataTable columns={COLUMNS} minWidth={TABLE_MIN_WIDTH} cardRow>
      <Together view={view} />
      {view.donors.map((row) => (
        <Table.Tr key={row.host}>
          <Table.Td className="cellName">
            <Text fw={500} className="cellName">
              <Seams text={row.host} />
            </Text>
          </Table.Td>
          {/* Мера здоровья ТЗ («403/429/5xx/таймаут — 4,2% за последние 480 запросов») шире
              колонки обхода — переносом, а не поверх соседней колонки. */}
          <Table.Td data-label="Обход" className="wrapCell" style={WHOLE_LINE}>
            <CrawlState crawl={row.crawl} />
          </Table.Td>
          <FoundCells found={row.findings} />
          <TallyCells tally={row.tally} />
          {/* Чего не хватает и почему поставщик не считается — переносом: в одну строку
              не помещаются ни в колонку, ни в карточку телефона. */}
          <Table.Td data-label="Приёмка" className="wrapCell" style={WHOLE_LINE}>
            <Verdict tally={row.tally} view={view} supplier={row.supplier} />
          </Table.Td>
        </Table.Tr>
      ))}
    </DataTable>
  );
}

export function AcceptanceReport() {
  const { data, error } = useQuery({ queryKey: ACCEPTANCE_KEY, queryFn: fetchAcceptance });
  if (data === undefined) {
    return error ? (
      <Alert color="red" title="Приёмка не загрузилась">
        {refusalOf(error)}
      </Alert>
    ) : (
      <Loader aria-label="Считаем приёмку" m="md" />
    );
  }
  return (
    <Card className="glassPanel" p="lg">
      <Stack gap="sm">
        <PageHead title="Приёмка по донорам" hint={ABOUT} />
        <Text size="sm">
          Порог ТЗ: у донора {formatNumber(data.target_with_address)} рекламодателей с адресом, и
          письмо ушло {formatPercent(data.target_sent_share)} из них.
        </Text>
        {data.donors.length === 0 ? (
          <Text size="sm" c="dimmed">
            Обойдённых доноров нет: приёмка считается по обходу, а он начинается на вкладке
            «Проверка», в панели «Обход доноров».
          </Text>
        ) : (
          <>
            <Rows view={data} />
            <Shown view={data} />
          </>
        )}
      </Stack>
    </Card>
  );
}
