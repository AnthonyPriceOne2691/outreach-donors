/**
 * «Приёмка по донорам» — порог ТЗ Этапа 2 по каждому обойдённому донору.
 *
 * Сверка ТЗ 11.10.2026: «по донору 100 рекламодателей с контактами, дедуплицировано,
 * письма ушли 70% из них» — этих чисел не было нигде. Строка — донор, его последний
 * обход (тем же видом, что на панели «Обход доноров», с долей закрытых страниц),
 * сколько у него рекламодателей, сколько из них с адресом, скольким ушло письмо
 * и пройден ли порог. Чего не хватает — словами: «с адресом 34 из 100».
 *
 * **Все доноры вместе — строкой над таблицей, справочно**: критерий ТЗ — по донору, а
 * сумма отвечает на оговорку самого ТЗ («Арифметика», п. 6) — по разным доменам, без
 * поставщиков. Пороги — от сервера.
 *
 * **С адресом и «ушло» — среди переведённых** (проверка ветки 12.10.2026): домен, снятый
 * стоп-листом, с адресом от Этапа 1 приёмку не украшает. **Поставщик — помечен**: его
 * рекламодателей не трогаем, порог ему не задача. Доноров больше, чем строк, —
 * «показано N из M».
 */

import { Alert, Badge, Card, Group, Loader, Stack, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { fetchAcceptance } from '../api/acceptance';
import type { AcceptanceView, Tally } from '../api/acceptance';
import { refusalOf } from '../api/client';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { PageHead } from '../components/PageHead';
import { Seams } from '../components/Seams';
import { formatNumber, formatPercent } from '../format';
import { CrawlState } from './CrawlTable';

export const ACCEPTANCE_KEY = ['advertisers', 'acceptance'] as const;

const ABOUT =
  'Рекламодатель донора — его «куплено» и «пишем» последнего обхода, без «не пишем»: тех же ' +
  'переводит «Перевести в рекламодатели». Один домен у донора — один раз; домен двух доноров ' +
  'считается у каждого, а в строке «Все доноры вместе» — один раз. С адресом — среди ' +
  'переведённых, адрес в базе. Письмо ушло — платформа приняла хоть одно письмо, отказ ' +
  'доставки тоже ушедшее письмо. Доля — от тех, что с адресом. Поставщик помечен: его ' +
  'рекламодателей не трогаем.';

/** Колонки: донору — остаток; обходу — как «Последний обход» панели обхода (17rem);
 *  числам — по «1 000 · 100%»; приёмке — по «ушло 69,9% из 70%». */
const COLUMNS: Column[] = [
  { title: 'Донор' },
  { title: 'Обход', width: '17rem' },
  { title: 'Рекламодателей', width: '9.5rem' },
  { title: 'С адресом', width: '8rem' },
  { title: 'Письмо ушло', width: '8.5rem' },
  { title: 'Приёмка', width: '12rem' },
];
const TABLE_MIN_WIDTH = 1100;

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

function Found({ tally }: { tally: Tally }) {
  return (
    <Stack gap={0} align="center">
      <span>{formatNumber(tally.found)}</span>
      {/* Не переведённые адреса не получат: искать их начнут после перевода. */}
      {tally.promoted < tally.found ? (
        <Text size="xs" c="dimmed">
          переведено {formatNumber(tally.promoted)}
        </Text>
      ) : null}
    </Stack>
  );
}

function Overall({ view }: { view: AcceptanceView }) {
  const all = view.overall;
  return (
    <Group gap="xs" align="center">
      <Text size="sm">Все доноры вместе — справочно, не критерий:</Text>
      <Text size="sm" c="dimmed">
        рекламодателей {formatNumber(all.found)}, с адресом {formatNumber(all.with_address)}, письмо
        ушло {sentLine(all)}; без поставщиков, домен — один раз
      </Text>
    </Group>
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
    <DataTable columns={COLUMNS} minWidth={TABLE_MIN_WIDTH}>
      {view.donors.map((row) => (
        <Table.Tr key={row.host}>
          <Table.Td className="cellName">
            <Text fw={500} className="cellName">
              <Seams text={row.host} />
            </Text>
          </Table.Td>
          <Table.Td data-label="Обход">
            <CrawlState crawl={row.crawl} />
          </Table.Td>
          <Table.Td data-label="Рекламодателей">
            <Found tally={row.tally} />
          </Table.Td>
          <Table.Td data-label="С адресом">{formatNumber(row.tally.with_address)}</Table.Td>
          <Table.Td data-label="Письмо ушло">{sentLine(row.tally)}</Table.Td>
          <Table.Td data-label="Приёмка">
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
            <Overall view={data} />
            <Rows view={data} />
            <Shown view={data} />
          </>
        )}
      </Stack>
    </Card>
  );
}
