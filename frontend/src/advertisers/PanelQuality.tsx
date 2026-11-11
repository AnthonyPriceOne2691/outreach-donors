/**
 * «Качество загрузок»: что дала каждая загрузка — дата, способ, итог скана, продающие ссылки и их
 * доля (тест-группа 31.10.2026).
 *
 * Строка — партия файлом или донор вручную; обходы кнопкой у доноров без записи о загрузке —
 * строкой на день: прежние загрузки партиями не записаны. Ночной обход — одной строкой, прошлые
 * ночи — её раскрытием (`QualityNight`, 11.11.2026): те же доноры каждую ночь читались строкой на
 * день как отдельные загрузки. Страница считается на сервере и живёт в адресе (`?quality_page=2`),
 * не задевая выборку окна сверху. Все домены строки — окном по кнопке: у партии их бывает десять
 * тысяч, и раскрывать их в таблице значило бы терять саму таблицу.
 *
 * **Способ один у всех строк — строкой над таблицей, колонки нет** (разведка 10.11.2026: самая широкая
 * колонка во всех строках говорила «обход без загрузки»). Один домен строки, который виден в способе
 * загрузки, тогда встаёт в «Доменов» — на место «1». Считается по строкам страницы: других страниц
 * экран не знает, и над таблицей тогда сказано «строк страницы».
 */

import {
  Alert,
  Card,
  Group,
  Modal,
  ScrollArea,
  Skeleton,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { fetchQuality, fetchQualityHosts } from '../api/advertiserPanel';
import type { QualityRow, QualityView } from '../api/advertiserPanel';
import { refusalOf } from '../api/client';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { InfoHint } from '../components/InfoHint';
import { PageSwitch, usePageParam } from '../components/PageSwitch';
import { Seams } from '../components/Seams';
import { formatNumber } from '../format';
import { CrawlState } from './CrawlTable';
import { NightRows } from './QualityNight';
import {
  NumberCells,
  QUALITY_KEY,
  WHOLE_LINE,
  dateWords,
  kindWords,
  methodWords,
} from './qualityRows';

const ABOUT_LINES = [
  'Строка — загрузка: партия файлом или донор, заведённый вручную. Обход кнопкой у донора без записи о загрузке — строкой на день, по Москве.',
  'Ночной обход — одна строка: в ней итог последней ночи, прошлые ночи раскрывает «ещё N ночей». Ночь проходит всех доноров, и доноров партий тоже.',
  'Повторный обход — ночной или кнопкой — проходит тех же доноров заново, это не новые загрузки: итог обновляется, кандидаты донора заменяются, решения людей сохраняются. Дублей нет.',
  'Новый рекламодатель — домен «куплено», которого до этой ночи обходы не находили и среди рекламодателей не было. У прошлых ночей этого числа нет: кандидатов донора заменяет следующая ночь.',
  'Страниц — по последнему пригодному обходу каждого донора: повтор заменяет итог, а не прибавляется. Продающая ссылка — ссылка на домен с вердиктом скоринга «куплено»; решение человека этих чисел не меняет. Прочерк — обходы строки посчитаны раньше этих чисел.',
];

const SHARE_HINT = (
  <InfoHint name="Что значит «% страниц»" width={320}>
    Доля просканированных страниц, на которых нашлась продающая ссылка: витрина с пятью офферами —
    одна страница. Прочерк — обходы строки посчитаны раньше этого числа; его дополнит пересчёт
    кандидатов.
  </InfoHint>
);

const METHOD = 'Способ загрузки';

/** Способ — по самому длинному: «файлом: имя-файла…» режется многоточием, у ночной строки под
 *  способом — что нового и раскрытие ночей, они переносятся. */
const COLUMNS: Column[] = [
  { title: 'Дата', width: '7rem' },
  { title: METHOD },
  { title: 'Доменов', width: '8.5rem' },
  { title: 'Страниц', width: '6rem' },
  { title: 'Продающих ссылок', width: '7.5rem' },
  { title: '% страниц', width: '7.5rem', hint: SHARE_HINT },
];
/** Способ один у всех строк — без его колонки. */
const NUMBER_COLUMNS = COLUMNS.filter((column) => column.title !== METHOD);
/** Уже — прокрутка: способ загрузки не уже 14rem и пять колонок чисел; без способа — пять чисел. */
const TABLE_MIN_WIDTH = 760;
const NUMBERS_MIN_WIDTH = 600;

/** Обход — ширина «Последнего обхода» у таблицы доноров: значок и «i» в одну строку. */
const HOST_COLUMNS: Column[] = [
  { title: 'Домен' },
  { title: 'Обход', width: '19rem' },
  { title: 'Продающих ссылок', width: '7.5rem' },
];
const HOSTS_MIN_WIDTH = 620;

/** Сколько строк-заготовок держит высоту таблицы до первого ответа. */
const SKELETON_ROWS = 5;

/** Заголовок блока с пояснением. Пояснение — окошком по нажатию, а не всплывающей подсказкой
 *  (правило 10.11.2026: длиннее ~75 знаков — окошком): пять абзацев выше окна телефона, а
 *  подсказку не прокрутить и не удержать. В окошке — прокрутка не выше полэкрана, ширина — в
 *  телефон 390 px с полями, как у доли ложных (`FalseShare`). */
function Head() {
  return (
    <Group gap="sm" align="center" wrap="nowrap">
      <Title order={3}>Качество загрузок</Title>
      <InfoHint name="Как устроен экран «Качество загрузок»" width={340} interactive>
        <ScrollArea.Autosize
          mah="50vh"
          type="auto"
          offsetScrollbars
          viewportProps={{ tabIndex: 0 }}
        >
          <Stack gap={6}>
            {ABOUT_LINES.map((line) => (
              <Text key={line} size="sm">
                {line}
              </Text>
            ))}
          </Stack>
        </ScrollArea.Autosize>
      </InfoHint>
    </Group>
  );
}

/** Способ, одинаковый у всех строк, — или `null`, когда строки загружены по-разному. У одной
 *  строки «одинаковое у всех» — любая колонка: такой таблице колонка остаётся. Ночная строка на
 *  странице одна, и с ней способы разные: её колонка способа есть всегда. */
function sharedMethod(rows: QualityRow[]): string | null {
  const [first, ...rest] = rows;
  if (first === undefined || rest.length === 0) return null;
  const method = methodWords(first);
  return rest.every((row) => methodWords(row) === method) ? method : null;
}

function HostsWindow({ row, onClose }: { row: QualityRow; onClose: () => void }) {
  const [page, setPage] = useState(1);
  const key = [...QUALITY_KEY, 'hosts', row.kind, row.load_id, row.day, page];
  const { data, error } = useQuery({
    queryKey: key,
    queryFn: () => fetchQualityHosts(row, page),
    placeholderData: keepPreviousData,
  });
  return (
    <Modal
      opened
      onClose={onClose}
      title={`Домены: ${kindWords(row)}, ${dateWords(row)}`}
      size="xl"
    >
      <Stack gap="sm">
        {error ? (
          <Alert color="red" title="Домены не загрузились">
            {refusalOf(error)}
          </Alert>
        ) : null}
        <DataTable
          columns={HOST_COLUMNS}
          minWidth={HOSTS_MIN_WIDTH}
          className="headsWrap"
          cardRow
          label="Домены строки"
        >
          {(data?.rows ?? []).map((host) => (
            <Table.Tr key={host.host}>
              <Table.Td className="cellName">
                <Text fw={500} className="cellName">
                  <Seams text={host.host} />
                </Text>
              </Table.Td>
              <Table.Td data-label="Обход" className="wrapCell crawlCell">
                {host.crawl === null ? (
                  <Text size="sm" c="dimmed">
                    обхода не было
                  </Text>
                ) : (
                  <CrawlState crawl={host.crawl} compact />
                )}
              </Table.Td>
              <Table.Td data-label="Продающих ссылок">{formatNumber(host.selling_links)}</Table.Td>
            </Table.Tr>
          ))}
        </DataTable>
        {data === undefined ? null : (
          <PageSwitch
            label="Страницы доменов"
            page={page}
            pages={Math.ceil(data.total / data.limit)}
            onChange={setPage}
          />
        )}
      </Stack>
    </Modal>
  );
}

interface RowsProps {
  rows: QualityRow[];
  /** Способ один у всех строк: его колонки нет, и один домен строки — в «Доменов». */
  shared: boolean;
  onHosts: (row: QualityRow) => void;
}

/** Способ без имени файла и домена — «обход кнопкой»: на телефоне он встаёт в строку даты, а не
 *  своей строкой с подписью. С ночной строкой на странице способы разные, колонка способа есть
 *  у каждой карточки, и без этого карточка дня была бы на треть выше, чем до ночной строки. */
function shortMethod(row: QualityRow): boolean {
  return row.kind === 'crawl' && row.host === null;
}

/** Ячейка способа: длинный — многоточием в одну строку, целиком — в `title`; на телефоне — во всю
 *  ширину карточки, короткий — рядом с датой. */
function MethodCell({ row }: { row: QualityRow }) {
  const words = (
    <Text size="sm" truncate="end" title={kindWords(row)} className="qualityMethod">
      {kindWords(row)}
    </Text>
  );
  return shortMethod(row) ? (
    <Table.Td className="wrapCell">{words}</Table.Td>
  ) : (
    <Table.Td data-label="Способ" className="wrapCell" style={WHOLE_LINE}>
      {words}
    </Table.Td>
  );
}

function Rows({ rows, shared, onHosts }: RowsProps) {
  return (
    <>
      {rows.map((row) => {
        // У сервера старше экрана поля `night` нет: его строки — загрузки и дни, как раньше.
        const night = row.night ?? null;
        return night === null ? (
          <Table.Tr
            key={row.load_id === null ? `day-${row.day}` : `load-${row.load_id}`}
            data-short={(!shared && shortMethod(row)) || undefined}
          >
            <Table.Td className="cellName">{dateWords(row)}</Table.Td>
            {shared ? null : <MethodCell row={row} />}
            <NumberCells row={row} lone={shared} onHosts={onHosts} />
          </Table.Tr>
        ) : (
          <NightRows key="night" row={row} night={night} span={COLUMNS.length} onHosts={onHosts} />
        );
      })}
    </>
  );
}

function Placeholder() {
  return (
    <>
      {Array.from({ length: SKELETON_ROWS }, (_, line) => (
        <Table.Tr key={line}>
          {COLUMNS.map((column) => (
            <Table.Td key={column.title}>
              <Skeleton height={14} radius="sm" />
            </Table.Td>
          ))}
        </Table.Tr>
      ))}
    </>
  );
}

/** Способ, одинаковый у всех строк, — строкой над таблицей; у страницы из нескольких — «строк
 *  страницы»: других страниц экран не знает. */
function MethodLine({ method, paged }: { method: string | null; paged: boolean }) {
  if (method === null) return null;
  return (
    <Text size="sm" className="qualityMethod">
      {METHOD} во всех строках{paged ? ' страницы' : ''}: {method}
    </Text>
  );
}

interface TableProps {
  data: QualityView | undefined;
  stale: boolean;
  onHosts: (row: QualityRow) => void;
}

/** Таблица: до первого ответа — заготовка строк; способ один у всех строк — без его колонки. На
 *  телефоне числа карточки — двумя равными колонками (`qualityTable`): у трёх колонка не уже своего
 *  содержимого, и «Страниц» прыгала от карточки к карточке. */
function QualityTable({ data, stale, onHosts }: TableProps) {
  const method = data === undefined ? null : sharedMethod(data.rows);
  const shared = method !== null;
  return (
    <>
      <MethodLine method={method} paged={data !== undefined && data.total > data.limit} />
      <DataTable
        columns={shared ? NUMBER_COLUMNS : COLUMNS}
        minWidth={shared ? NUMBERS_MIN_WIDTH : TABLE_MIN_WIDTH}
        className="allCentered headsWrap qualityTable"
        cardRow
        stale={stale}
        label="Качество загрузок"
      >
        {data === undefined ? (
          <Placeholder />
        ) : (
          <Rows rows={data.rows} shared={shared} onHosts={onHosts} />
        )}
      </DataTable>
    </>
  );
}

export function PanelQuality() {
  const [page, setPage] = usePageParam('quality_page');
  const [opened, setOpened] = useState<QualityRow | null>(null);
  const query = useQuery({
    queryKey: [...QUALITY_KEY, page],
    queryFn: () => fetchQuality(page),
    placeholderData: keepPreviousData,
  });
  const { data, error } = query;
  return (
    <Card className="glassPanel" p="lg">
      <Stack gap="sm">
        <Head />
        {error ? (
          <Alert color="red" title="Качество загрузок не загрузилось">
            {refusalOf(error)}
          </Alert>
        ) : null}
        {data !== undefined && data.total === 0 ? (
          <Text size="sm" c="dimmed">
            Загрузок и обходов ещё не было: доноров заводят на вкладке «Обход доноров».
          </Text>
        ) : (
          <QualityTable data={data} stale={query.isPlaceholderData} onHosts={setOpened} />
        )}
        {data === undefined ? null : (
          <PageSwitch
            label="Страницы качества загрузок"
            page={page}
            pages={Math.ceil(data.total / data.limit)}
            onChange={setPage}
          />
        )}
      </Stack>
      {opened === null ? null : <HostsWindow row={opened} onClose={() => setOpened(null)} />}
    </Card>
  );
}
