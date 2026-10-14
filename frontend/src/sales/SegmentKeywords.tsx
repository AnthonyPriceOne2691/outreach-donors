/**
 * Ключи сбора словами сервера — в смете до запуска и в отчёте прогона.
 *
 * **Взятые и отсеянные — оба списка** (проверка прода 14.10: из одиннадцати ключей взяли три, а
 * экран показывал только отсеянных, и человек не видел, за что платит выдача). Отсев — только
 * мусор и точный повтор, с причиной; почти повтор взят, рядом — на какой ключ он похож.
 *
 * **В отчёте — и что вышло с покупкой**: выдача не пришла (заплачено, а провайдер не отдал) и
 * выдача пуста (по ключу ничего не нашлось) — разные исходы, и сказаны они разными словами.
 */

import { List, SimpleGrid, Stack, Text } from '@mantine/core';

import { formatNumber } from '../format';
import type { SegmentRunDetail } from './segmentApi';

interface Lists {
  taken: string[];
  notes: Record<string, string>;
  rejected: Record<string, string>;
}

function Phrases({ title, rows }: { title: string; rows: [string, string | undefined][] }) {
  if (rows.length === 0) return null;
  return (
    <Stack gap={2}>
      <Text size="sm">{title}</Text>
      <List size="sm" spacing={2} aria-label={title}>
        {rows.map(([phrase, why]) => (
          <List.Item key={phrase}>
            {why === undefined ? `«${phrase}»` : `«${phrase}» — ${why}`}
          </List.Item>
        ))}
      </List>
    </Stack>
  );
}

/** Взятые ключи с пометками и отсеянные с причиной — рядом, на телефоне друг под другом. */
export function KeywordLists({ taken, notes, rejected }: Lists) {
  const cut = Object.entries(rejected);
  const total = taken.length + cut.length;
  return (
    <SimpleGrid cols={{ base: 1, sm: cut.length > 0 ? 2 : 1 }} spacing="sm" verticalSpacing="xs">
      <Phrases
        title={`Взято ключей: ${formatNumber(taken.length)} из ${formatNumber(total)}`}
        rows={taken.map((phrase) => [phrase, notes[phrase]])}
      />
      <Phrases title={`Отсеяно ключей: ${formatNumber(cut.length)}`} rows={cut} />
    </SimpleGrid>
  );
}

/** Ключи прогона: как их видел человек на запуске — и что вышло с покупкой выдачи. */
export function SegmentKeywords({ detail }: { detail: SegmentRunDetail }) {
  const found = detail.spend?.keywords;
  if (found === undefined) return null;
  return (
    <Stack gap={6}>
      <Text size="sm" fw={500}>
        Ключи прогона
      </Text>
      <KeywordLists taken={found.taken} notes={found.notes} rejected={found.rejected} />
      <Phrases
        title="Выдача не пришла — заплачено, а провайдер не отдал"
        rows={found.lost.map((phrase) => [phrase, undefined])}
      />
      <Phrases
        title="По ключу ничего не нашлось"
        rows={found.empty.map((phrase) => [phrase, undefined])}
      />
    </Stack>
  );
}
