/**
 * Слова и ячейки строки «Качества загрузок» — общие у строки загрузки (`PanelQuality`) и у ночной
 * строки с её раскрытием (`QualityNight`): две копии «Доменов» и чисел разошлись бы первой правкой.
 */

import { Button, Table, Text } from '@mantine/core';

import type { QualityRow } from '../api/advertiserPanel';
import { Seams } from '../components/Seams';
import { formatDate, formatNumber, formatPercent } from '../format';

export const QUALITY_KEY = ['advertisers', 'panel', 'quality'] as const;

/** Способ загрузки на телефоне — во всю ширину карточки: имя файла длиннее колонки чисел. В таблице
 *  сетки нет, и правило ни на что не влияет. */
export const WHOLE_LINE = { gridColumn: '1 / -1' } as const;

/** Способ без домена строки: домен — её подробность, а не способ. Обход без записи о загрузке —
 *  «кнопкой»: его поставил человек, а ночной обход стоит своей строкой (11.11.2026). */
export function methodWords(row: QualityRow): string {
  if (row.kind === 'bulk') return `файлом: ${row.file_name ?? 'без имени'}`;
  if (row.kind === 'manual') return 'вручную';
  return row.kind === 'night' ? 'ночной обход — каждую ночь' : 'обход кнопкой';
}

/** Способ загрузки словами: файл называется по имени, ручной домен виден сразу. */
export function kindWords(row: QualityRow): string {
  if (row.kind === 'bulk' || row.kind === 'night') return methodWords(row);
  if (row.kind === 'manual') return `вручную: ${row.host ?? 'домен'}`;
  return row.host === null ? methodWords(row) : `${methodWords(row)}: ${row.host}`;
}

/** Дата строки: у партии — день загрузки, у обходов без загрузки и ночи — их день: он посчитан по
 *  Москве, и браузер в другом поясе не должен его сдвигать. */
export function dateWords(row: QualityRow): string {
  return row.day === null ? formatDate(row.at) : row.day.split('-').reverse().join('.');
}

interface CellsProps {
  row: QualityRow;
  /** Один домен строки — им самим, а не «1»: когда в способе загрузки его не видно. */
  lone: boolean;
  /** Открыть все домены строки; без него — число: у прошлой ночи окно показало бы нынешние обходы
   *  её доноров, а не ту ночь. */
  onHosts?: ((row: QualityRow) => void) | undefined;
}

/** «Доменов»: все — окном по кнопке; один — им самим (`lone`), по швам, а не обрезанным; иначе —
 *  число. */
function Domains({ row, lone, onHosts }: CellsProps) {
  if (row.donors > 1 && onHosts !== undefined) {
    return (
      <Button
        variant="subtle"
        size="compact-sm"
        className="press"
        aria-label={`Домены строки: ${kindWords(row)}`}
        onClick={() => onHosts(row)}
      >
        {formatNumber(row.donors)} · список
      </Button>
    );
  }
  if (lone && row.host !== null) {
    return (
      <Text size="sm" className="cellName">
        <Seams text={row.host} />
      </Text>
    );
  }
  return formatNumber(row.donors);
}

/** Четыре числа строки — теми же колонками у загрузки, ночной строки и прошлой ночи. Чего нет —
 *  прочерк, а не ноль: ноль сказал бы «искали и не нашли». */
export function NumberCells({ row, lone, onHosts }: CellsProps) {
  return (
    <>
      <Table.Td data-label="Доменов" className="wrapCell">
        <Domains row={row} lone={lone} onHosts={onHosts} />
      </Table.Td>
      <Table.Td data-label="Страниц">{formatNumber(row.pages)}</Table.Td>
      <Table.Td data-label="Продающих ссылок">
        {row.selling_links === null ? '—' : formatNumber(row.selling_links)}
      </Table.Td>
      <Table.Td data-label="% страниц">
        {row.share === null ? '—' : formatPercent(row.share)}
      </Table.Td>
    </>
  );
}
