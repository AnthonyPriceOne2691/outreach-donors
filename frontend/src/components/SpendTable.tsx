/**
 * Расход статьями и единицы счёта провайдеров — одно на экраны «Расход» и «Отчёт
 * прогона» (11.10.2026). Две копии таблицы и подписей единиц разошлись бы при первой
 * правке: сначала шириной колонок, потом словами, и один и тот же расход читался бы
 * на двух экранах по-разному.
 *
 * **Единица у каждого провайдера — своя**: Ahrefs берёт юнитами, модель — токенами,
 * отправка считает письма, выдача платится деньгами. «Юн.» у всех подряд называло
 * токены модели юнитами Ahrefs.
 */

import { Badge, Table, Text } from '@mantine/core';

import { operationTitle, usageProvider } from '../api/labels';
import type { ArticleCard } from '../api/types';
import { formatNumber, formatUsd, plural } from '../format';
import { TableColumns } from './TableColumns';
import type { TableColumn } from './TableColumns';

/** Единица в ячейке таблицы — сокращением. */
const UNIT_TITLES: Record<string, string> = {
  ahrefs: 'юн.',
  llm: 'ток.',
  hunter: 'запр.',
};

/** Та же единица словом — для плитки, где под числом есть место. */
const UNITS: [string, string, string] = ['юнит', 'юнита', 'юнитов'];
const UNIT_WORDS: Record<string, [string, string, string]> = {
  ahrefs: UNITS,
  llm: ['токен', 'токена', 'токенов'],
  email: ['письмо', 'письма', 'писем'],
  hunter: ['запрос', 'запроса', 'запросов'],
};

/** «1 письмо, 4 письма, 5 писем» — у сокращений склонять нечего. */
function unitOf(provider: string, count: number): string {
  if (provider !== 'email') return UNIT_TITLES[provider] ?? 'юн.';
  return plural(count, 'письмо', 'письма', 'писем');
}

/** Единица словом, согласованная с числом: «6 416 юнитов», «4 письма». */
export function unitWord(provider: string, count: number): string {
  const [one, few, many] = UNIT_WORDS[provider] ?? UNITS;
  return plural(count, one, few, many);
}

/** Плитка провайдера: число — крупно, единица и деньги — подписью под ним. */
export function tileOf(
  provider: string,
  units: number,
  amount: number,
): { value: string; hint: string | undefined } {
  if (units > 0) {
    const unit = unitWord(provider, units);
    return {
      value: formatNumber(units),
      hint: amount > 0 ? `${unit} и ${formatUsd(amount)}` : unit,
    };
  }
  // У провайдеров разная валюта счёта: Ahrefs берёт юнитами, источник
  // выдачи — деньгами, модель — токенами. Показывать «0.00 $» там, где
  // платят не деньгами, значит уверять, что трат не было: так экран
  // и врал про выдачу до этого среза.
  if (amount > 0) return { value: formatUsd(amount), hint: undefined };
  return { value: '—', hint: 'трат не было' };
}

/** Колонки статей — по самому длинному, замеренному шрифтом экрана 25.09.2026:
 *  значок «метрики Ahrefs» — 116 px (на телефоне он ужимался до «метрики Ahr…»),
 *  «12 345 678 ток.» — 104, «1 234,56 $» — 69; статья — «переписывание писем продаж».
 *  Плюс 32 px полей ячейки. **Ширина задана у всех**, и запас панели делится между
 *  колонками пропорционально: до 09.10.2026 «Статья» была остатком и забирала ~650 px
 *  под названия в 150–210 px, а колонки чисел жались к правому краю (аудит экранов). */
const COLUMNS: TableColumn[] = [
  { title: 'Статья', width: '15rem' },
  { title: 'Провайдер', width: '9.5rem' },
  { title: 'Запросов', width: '7rem' },
  { title: 'Единиц', width: '9rem' },
  { title: 'Денег', width: '7rem' },
];
const TABLE_MIN_WIDTH = 720;

/** Статьи расхода: что, у кого, сколько запросов, единиц и денег. */
export function SpendTable({ articles }: { articles: ArticleCard[] }) {
  return (
    <Table.ScrollContainer minWidth={TABLE_MIN_WIDTH} type="native" className="scrollSlim">
      <Table
        className="dataTable fixedTable"
        layout="fixed"
        tabularNums
        verticalSpacing="sm"
        horizontalSpacing="md"
      >
        <TableColumns columns={COLUMNS} />
        <Table.Tbody>
          {articles.map((article) => (
            <Table.Tr key={`${article.provider}-${article.operation}`}>
              <Table.Td>
                <Text fw={500} className="cellName">
                  {operationTitle(article.operation)}
                </Text>
              </Table.Td>
              <Table.Td>
                <Badge variant="light" color={usageProvider(article.provider).color}>
                  {usageProvider(article.provider).title}
                </Badge>
              </Table.Td>
              <Table.Td>{formatNumber(article.calls)}</Table.Td>
              <Table.Td>
                {article.units > 0
                  ? `${formatNumber(article.units)} ${unitOf(article.provider, article.units)}`
                  : '—'}
              </Table.Td>
              <Table.Td>
                {Number(article.amount_usd) > 0 ? formatUsd(article.amount_usd) : '—'}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
