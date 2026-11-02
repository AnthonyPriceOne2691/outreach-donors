/**
 * «Качество загрузок»: что дала каждая загрузка — дата, способ, итог скана, продающие ссылки и их
 * доля (тест-группа 31.10.2026).
 *
 * Строка — партия файлом или донор вручную; обходы без записи о загрузке — строкой на день: их
 * ставят кнопкой, и прежние загрузки партиями не записаны. Страница считается на сервере и живёт
 * в адресе (`?quality_page=2`), не задевая выборку окна сверху. Все домены строки — окном по кнопке:
 * у партии их бывает десять тысяч, и раскрывать их в таблице значило бы терять саму таблицу.
 */

import { Alert, Button, Card, Modal, Skeleton, Stack, Table, Text } from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { fetchQuality, fetchQualityHosts } from '../api/advertiserPanel';
import type { QualityRow } from '../api/advertiserPanel';
import { refusalOf } from '../api/client';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { InfoHint } from '../components/InfoHint';
import { PageHead } from '../components/PageHead';
import { PageSwitch, usePageParam } from '../components/PageSwitch';
import { Seams } from '../components/Seams';
import { formatDate, formatNumber, formatPercent } from '../format';
import { CrawlState } from './CrawlTable';

export const QUALITY_KEY = ['advertisers', 'panel', 'quality'] as const;

const ABOUT_LINES = [
  'Строка — загрузка: партия файлом или донор, заведённый вручную. Обходы без записи о загрузке — обход кнопкой и загрузки раньше этого экрана — строкой на день, по Москве.',
  'Страниц — просканировано последним пригодным обходом каждого донора с минуты загрузки: повтор заменяет итог, а не прибавляется.',
  'Продающая ссылка — ссылка на домен с вердиктом скоринга «куплено». Решение человека этих чисел не меняет: это мера обхода, а не очереди.',
];

const ABOUT = (
  <Stack gap={6}>
    {ABOUT_LINES.map((line) => (
      <span key={line}>{line}</span>
    ))}
  </Stack>
);

const SHARE_HINT = (
  <InfoHint name="Что значит «% страниц»" width={320}>
    Доля просканированных страниц, на которых нашлась продающая ссылка: витрина с пятью офферами —
    одна страница. Прочерк — обходы строки посчитаны раньше этого числа; его дополнит пересчёт
    кандидатов.
  </InfoHint>
);

/** Способ — по самому длинному: «обход без загрузки: имя-файла…» режется многоточием. */
const COLUMNS: Column[] = [
  { title: 'Дата', width: '7rem' },
  { title: 'Способ загрузки' },
  { title: 'Доменов', width: '8.5rem' },
  { title: 'Страниц', width: '6rem' },
  { title: 'Продающих ссылок', width: '7.5rem' },
  { title: '% страниц', width: '7.5rem', hint: SHARE_HINT },
];
/** Уже — прокрутка: способ загрузки не уже 14rem и пять колонок чисел. */
const TABLE_MIN_WIDTH = 760;

/** Обход — ширина «Последнего обхода» у таблицы доноров: значок и «i» в одну строку. */
const HOST_COLUMNS: Column[] = [
  { title: 'Домен' },
  { title: 'Обход', width: '19rem' },
  { title: 'Продающих ссылок', width: '7.5rem' },
];
const HOSTS_MIN_WIDTH = 620;

/** Способ загрузки на телефоне — во всю ширину карточки: имя файла длиннее колонки чисел. В таблице
 *  сетки нет, и правило ни на что не влияет. */
const WHOLE_LINE = { gridColumn: '1 / -1' } as const;

/** Сколько строк-заготовок держит высоту таблицы до первого ответа. */
const SKELETON_ROWS = 5;

/** Способ загрузки словами: файл называется по имени, ручной домен виден сразу. */
function kindWords(row: QualityRow): string {
  if (row.kind === 'bulk') return `файлом: ${row.file_name ?? 'без имени'}`;
  if (row.kind === 'manual') return `вручную: ${row.host ?? 'домен'}`;
  return row.host === null ? 'обход без загрузки' : `обход без загрузки: ${row.host}`;
}

function shareWords(share: number | null): string {
  return share === null ? '—' : formatPercent(share);
}

/** Дата строки: у партии — день загрузки, у обходов без загрузки — их день: он посчитан по Москве,
 *  и браузер в другом поясе не должен его сдвигать. */
function dateWords(row: QualityRow): string {
  return row.day === null ? formatDate(row.at) : row.day.split('-').reverse().join('.');
}

function HostsWindow({ row, onClose }: { row: QualityRow; onClose: () => void }) {
  const [page, setPage] = useState(1);
  const key = [...QUALITY_KEY, 'hosts', row.load_id, row.day, page];
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

function Rows({ rows, onHosts }: { rows: QualityRow[]; onHosts: (row: QualityRow) => void }) {
  return (
    <>
      {rows.map((row) => (
        <Table.Tr key={row.load_id === null ? `day-${row.day}` : `load-${row.load_id}`}>
          <Table.Td className="cellName">{dateWords(row)}</Table.Td>
          <Table.Td data-label="Способ" className="wrapCell" style={WHOLE_LINE}>
            <Text size="sm" truncate="end" title={kindWords(row)}>
              {kindWords(row)}
            </Text>
          </Table.Td>
          <Table.Td data-label="Доменов">
            {row.donors > 1 ? (
              <Button
                variant="subtle"
                size="compact-sm"
                className="press"
                aria-label={`Домены строки: ${kindWords(row)}`}
                onClick={() => onHosts(row)}
              >
                {formatNumber(row.donors)} · список
              </Button>
            ) : (
              formatNumber(row.donors)
            )}
          </Table.Td>
          <Table.Td data-label="Страниц">{formatNumber(row.pages)}</Table.Td>
          <Table.Td data-label="Продающих ссылок">{formatNumber(row.selling_links)}</Table.Td>
          <Table.Td data-label="% страниц">{shareWords(row.share)}</Table.Td>
        </Table.Tr>
      ))}
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
        <PageHead title="Качество загрузок" hint={ABOUT} />
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
          <DataTable
            columns={COLUMNS}
            minWidth={TABLE_MIN_WIDTH}
            className="allCentered headsWrap"
            cardRow
            stale={query.isPlaceholderData}
            label="Качество загрузок"
          >
            {data === undefined ? <Placeholder /> : <Rows rows={data.rows} onHosts={setOpened} />}
          </DataTable>
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
