/**
 * Как судья понял описание сегмента (судья сегмента v4, `report.scope`): страны, где по описанию
 * работают компании сегмента, и виды сайтов, которые он просит. По ним судья сверяет каждый сайт,
 * и оператор должен видеть, с чем.
 *
 * **Разбор — в «i» у заголовка прогона, сбой — строкой отчёта** (замечание владельца 10.11.2026:
 * «пояснения — не простынёй, а свёрнуто или в «i»»). Удачный разбор — то, как устроен прогон: его
 * читают раз, и он стоит в «i» рядом с режимом судьи (`SegmentRunHint`). Сбой разбора — другое:
 * судья тогда не решает по месту ничего, сайты ждут человека «на проверке», а следующий проход
 * спросит снова. По нему решают сейчас — и он на экране, словами сервера.
 */

import { Text } from '@mantine/core';

import { capitalized } from '../format';

/** Слова разбора из отчёта прогона — и удачного, и сбоя; разбора нет (прогон до судьи v4) —
 *  `null`. */
export function scopeWords(report: Record<string, unknown>): string | null {
  const scope = report.scope;
  if (typeof scope !== 'object' || scope === null) return null;
  const words = (scope as { words?: unknown }).words;
  return typeof words === 'string' && words.trim() !== '' ? words : null;
}

/** Разбор не получен — модель не ответила или ответ не той формы: почему — в `failed`. */
export function scopeFailed(report: Record<string, unknown>): boolean {
  const scope = report.scope;
  return (
    typeof scope === 'object' && scope !== null && Boolean((scope as { failed?: unknown }).failed)
  );
}

/** Сбой разбора — строкой отчёта словами сервера; удачный разбор строкой не стоит. */
export function SegmentScopeLine({ report }: { report: Record<string, unknown> }) {
  const words = scopeFailed(report) ? scopeWords(report) : null;
  if (words === null) return null;
  return (
    <Text size="sm" c="dimmed">
      {`${capitalized(words)}.`}
    </Text>
  );
}
