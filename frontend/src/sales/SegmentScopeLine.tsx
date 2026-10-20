/**
 * Как судья понял описание сегмента — строкой отчёта прогона (судья сегмента v4, `report.scope`):
 * страны, где по описанию работают компании сегмента, и виды сайтов, которые он просит. По ним судья
 * сверяет каждый сайт, и оператор должен видеть, с чем. Сбой разбора — тоже словами сервера:
 * сайты тогда ждут человека, а следующий проход спросит снова.
 */

import { Text } from '@mantine/core';

/** Слова разбора из отчёта прогона; разбора нет (прогон до судьи v4) — `null`. */
export function scopeWords(report: Record<string, unknown>): string | null {
  const scope = report.scope;
  if (typeof scope !== 'object' || scope === null) return null;
  const words = (scope as { words?: unknown }).words;
  return typeof words === 'string' && words.trim() !== '' ? words : null;
}

export function SegmentScopeLine({ report }: { report: Record<string, unknown> }) {
  const words = scopeWords(report);
  if (words === null) return null;
  return (
    <Text size="sm" c="dimmed">
      Как судья понял описание: {words}.
    </Text>
  );
}
