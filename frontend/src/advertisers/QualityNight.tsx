/**
 * Ночной обход в «Качестве загрузок» — одной строкой (11.11.2026).
 *
 * Обход каждую ночь заново проходит тех же доноров, и «строка на день» давала столько одинаковых
 * строк, сколько прошло ночей: их читали отдельными загрузками и искали в них дубли. Строка одна:
 * итог последней ночи теми же колонками, что у загрузок, под способом — что нового она дала, а
 * прошлые ночи — раскрытием «ещё N ночей», порциями с сервера.
 *
 * **Что нового — словами, а не колонкой**: число есть только у последней ночи (кандидатов прошлых
 * заменил следующий обход), и колонка стояла бы столбцом прочерков. Числа нет — слов нет: «новых
 * нет» сказало бы «искали и не нашли».
 *
 * **Прошлые ночи — строками той же таблицы**, сразу под ночной: их числа встают в те же колонки.
 * Списка доменов у них нет: окно показало бы нынешние обходы доноров, а не ту ночь. Порция — её
 * размер называет сервер; дальше — «Показать ещё»: ночей прибавляется по одной в сутки.
 */

import { Alert, Button, Group, Skeleton, Stack, Table, Text } from '@mantine/core';
import { IconChevronDown, IconChevronUp } from '@tabler/icons-react';
import { useQueries } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';
import { useId, useState } from 'react';

import { fetchQualityNights } from '../api/advertiserPanel';
import type { QualityNight, QualityRow, QualityView } from '../api/advertiserPanel';
import { refusalOf } from '../api/client';
import { InfoHint } from '../components/InfoHint';
import { WholeRow } from '../components/WholeRow';
import { formatNumber, plural } from '../format';
import { NumberCells, QUALITY_KEY, WHOLE_LINE, dateWords, methodWords } from './qualityRows';

/** Строк-заготовок, пока едет первая порция: её настоящий размер назовёт сервер. */
const FIRST_PORTION = 7;

/** Что нового дала ночь — словами под способом. Числа нет — слов нет; обход ещё идёт — сказано:
 *  итог строки не окончательный. Число не отрывается от подписи (неразрывный пробел). */
export function nightNews(night: QualityNight): string | null {
  const parts: string[] = [];
  if (night.running) parts.push('обход ещё идёт');
  if (night.fresh === 0) parts.push('новых рекламодателей за\u00a0ночь нет');
  if (night.fresh !== null && night.fresh > 0) {
    parts.push(`новых рекламодателей за\u00a0ночь:\u00a0${formatNumber(night.fresh)}`);
  }
  return parts.length === 0 ? null : parts.join(' · ');
}

interface Past {
  rows: QualityRow[];
  /** Прошлых ночей всего и размер порции — словами сервера; до первого ответа пусто. */
  total: number | null;
  limit: number | null;
  loading: boolean;
  error: Error | null;
}

/** Чем ночь отличается от соседних: её день. */
function nightKey(row: QualityRow): string {
  return row.day ?? row.at;
}

/** Ночи всех порций — каждая один раз: начнись новая ночь между порциями, сдвиг показал бы ту же
 *  ночь дважды. */
function merged(results: UseQueryResult<QualityView>[]): QualityRow[] {
  const nights = new Map<string, QualityRow>();
  for (const row of results.flatMap((result) => result.data?.rows ?? [])) {
    nights.set(nightKey(row), row);
  }
  return [...nights.values()];
}

/** Порции — одним списком; сколько ночей всего и какой размер порции, говорит первая. */
function gather(results: UseQueryResult<QualityView>[]): Past {
  const first = results[0]?.data;
  const failed = results.find((result) => result.error !== null);
  return {
    rows: merged(results),
    total: first === undefined ? null : first.total,
    limit: first === undefined ? null : first.limit,
    loading: results.some((result) => result.isPending),
    error: failed === undefined ? null : failed.error,
  };
}

/** Прошлые ночи — столько порций, сколько раскрыто; свёрнуто — запросов нет. */
function usePastNights(portions: number): Past {
  return useQueries({
    queries: Array.from({ length: portions }, (_, index) => ({
      queryKey: [...QUALITY_KEY, 'nights', index + 1],
      queryFn: () => fetchQualityNights(index + 1),
    })),
    combine: gather,
  });
}

function nightsWord(count: number): string {
  return plural(count, 'ночь', 'ночи', 'ночей');
}

interface WordsProps {
  row: QualityRow;
  night: QualityNight;
  open: boolean;
  /** Строки раскрытия, которыми кнопка управляет. */
  controls: string | undefined;
  onToggle: () => void;
}

/** Ячейка способа у ночной строки: что это, что нового и раскрытие прошлых ночей. «ещё N ночей»
 *  и «Свернуть» — одна кнопка на одном месте. */
function NightWords({ row, night, open, controls, onToggle }: WordsProps) {
  const news = nightNews(night);
  const past = night.nights - 1;
  const Chevron = open ? IconChevronUp : IconChevronDown;
  return (
    <Stack gap={2} align="center">
      <Group gap={4} justify="center" wrap="nowrap">
        <Text size="sm" className="qualityMethod">
          {methodWords(row)}
        </Text>
        <InfoHint name="Что такое ночной обход" width={260}>
          Те же доноры каждую ночь: итог обновляется, решения сохраняются, дублей нет.
        </InfoHint>
      </Group>
      {news === null && past < 1 ? null : (
        <Group gap={6} justify="center">
          {news === null ? null : (
            <Text size="sm" c="dimmed" className="qualityNews">
              {news}
            </Text>
          )}
          {past < 1 ? null : (
            <Button
              variant="subtle"
              size="compact-sm"
              className="press qualityMore"
              aria-expanded={open}
              aria-controls={controls}
              rightSection={<Chevron size={14} aria-hidden />}
              onClick={onToggle}
            >
              {open ? 'Свернуть' : `ещё\u00a0${formatNumber(past)}\u00a0${nightsWord(past)}`}
            </Button>
          )}
        </Group>
      )}
    </Stack>
  );
}

/** Заготовка порции, пока она едет: раскрытие сразу встаёт в свою высоту. Вторая ячейка — способ:
 *  у прошлой ночи она пуста. */
function PastPlaceholder({ count, span }: { count: number; span: number }) {
  return Array.from({ length: count }, (_, line) => (
    <Table.Tr key={line} className="qualityPast" aria-hidden="true">
      {Array.from({ length: span }, (__, cell) =>
        cell === 1 ? (
          <Table.Td key={cell} className="qualityBlank" />
        ) : (
          <Table.Td key={cell}>
            <Skeleton height={12} radius="sm" />
          </Table.Td>
        ),
      )}
    </Table.Tr>
  ));
}

interface NightRowsProps {
  /** Ночная строка страницы качества: у неё есть `night`. */
  row: QualityRow;
  night: QualityNight;
  /** Колонок в таблице: отказ и «Показать ещё» — во всю ширину. */
  span: number;
  onHosts: (row: QualityRow) => void;
}

/** Ночная строка и, по нажатию, прошлые ночи под ней. Один донор ночи — им самим: в способе его
 *  не видно. */
export function NightRows({ row, night, span, onHosts }: NightRowsProps) {
  const [portions, setPortions] = useState(0);
  const pastId = useId();
  const past = usePastNights(portions);
  const open = portions > 0;
  const idOf = (line: QualityRow) => `${pastId}-${nightKey(line)}`;
  const rest = (past.total ?? night.nights - 1) - past.rows.length;
  const next = Math.min(past.limit ?? FIRST_PORTION, rest);
  return (
    <>
      <Table.Tr>
        <Table.Td className="cellName">
          {/* Дата строки — последняя ночь, и числа справа — её итог, а не сумма ночей: сказано.
              Подпись — в одну строку (`qualityLast`): в колонке даты два слова рвались. */}
          <Stack gap={0} align="center">
            <span>{dateWords(row)}</span>
            <Text size="xs" c="dimmed" className="qualityLast">
              последняя ночь
            </Text>
          </Stack>
        </Table.Td>
        <Table.Td data-label="Способ" className="wrapCell" style={WHOLE_LINE}>
          <NightWords
            row={row}
            night={night}
            open={open}
            controls={past.rows.length === 0 ? undefined : past.rows.map(idOf).join(' ')}
            onToggle={() => setPortions(open ? 0 : 1)}
          />
        </Table.Td>
        <NumberCells row={row} lone onHosts={onHosts} />
      </Table.Tr>
      {past.rows.map((line) => (
        <Table.Tr key={idOf(line)} id={idOf(line)} className="qualityPast">
          <Table.Td className="cellName">{dateWords(line)}</Table.Td>
          <Table.Td className="qualityBlank" />
          <NumberCells row={line} lone={false} />
        </Table.Tr>
      ))}
      {past.loading ? <PastPlaceholder count={next} span={span} /> : null}
      {past.error === null ? null : (
        <WholeRow span={span}>
          <Alert color="red" title="Прошлые ночи не загрузились">
            {refusalOf(past.error)}
          </Alert>
        </WholeRow>
      )}
      {!open || past.loading || past.error !== null || rest < 1 ? null : (
        <WholeRow span={span}>
          <Group gap="sm" justify="center">
            <Button
              variant="subtle"
              size="compact-sm"
              className="press"
              onClick={() => setPortions(portions + 1)}
            >
              {`Показать ещё\u00a0${formatNumber(next)}`}
            </Button>
            <Text size="sm" c="dimmed">
              {`показано ${formatNumber(past.rows.length)} из\u00a0${formatNumber(past.rows.length + rest)}`}
            </Text>
          </Group>
        </WholeRow>
      )}
    </>
  );
}
