/**
 * Журнал списков прогона по ссылкам конкурентов в его отчёте: что Ahrefs отдал по каждому
 * конкуренту, во что обошлось, сколько нового принёс каждый список, чей список пропущен
 * и почему, и что выброшено до проверки — числом по причине. Пишет его сервер в итог прогона (`stats.source`,
 * `backend/features/runs/competitors.py`); у прогона по ключам его нет — и строк нет.
 *
 * **Цена строки — по ответу и по смете рядом.** Смета считает строку моделью
 * (`ahrefs/refdomains.row_units`), а сколько списано, называет заголовок ответа. Разошлись —
 * это видно здесь, и это повод перемерить модель, а не гадать по счёту.
 */

import { Stack, Text } from '@mantine/core';

import type { RunCard } from '../api/types';
import { formatNumber, plural } from '../format';

/** Причина — кодом сервера, слова — на экране, как у порогов отчёта. */
const DROPPED: Record<string, string> = {
  unparsed: 'не домен',
  competitor: 'сам конкурент',
  dr_outside: 'DR вне порогов',
  repeat: 'повтор',
  donor: 'уже донор',
};

interface SourceList {
  target: string;
  rows: number;
  units: number;
  per_row: number | null;
  /** Сколько нового принёс список; нет — прогон встал до разбора списков, счёта не было. */
  kept?: number;
}

/** Конкурент, чей список Ahrefs не принял после хотя бы одного полученного: пропущен, а не
 *  остановил прогон; `reason` — словами сервера. */
interface Skipped {
  target: string;
  reason: string;
}

interface Source {
  row_units: number;
  targets: SourceList[];
  dropped: Record<string, number>;
  skipped: Skipped[];
}

function isList(value: unknown): value is SourceList {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as SourceList).target === 'string' &&
    typeof (value as SourceList).rows === 'number' &&
    typeof (value as SourceList).units === 'number'
  );
}

function isSkipped(value: unknown): value is Skipped {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as Skipped).target === 'string' &&
    typeof (value as Skipped).reason === 'string'
  );
}

/** Журнал из итога прогона; нет его или он не той формы — `null`: строк нет. */
export function sourceOf(run: RunCard): Source | null {
  const raw: unknown = run.stats?.['source'];
  if (typeof raw !== 'object' || raw === null) return null;
  const { row_units, targets, dropped, skipped } = raw as Partial<Record<keyof Source, unknown>>;
  if (!Array.isArray(targets)) return null;
  const counted = typeof dropped === 'object' && dropped !== null ? dropped : {};
  return {
    row_units: typeof row_units === 'number' ? row_units : 0,
    targets: (targets as unknown[]).filter(isList),
    dropped: Object.fromEntries(
      Object.entries(counted).filter(
        (entry): entry is [string, number] => typeof entry[1] === 'number',
      ),
    ),
    skipped: Array.isArray(skipped) ? (skipped as unknown[]).filter(isSkipped) : [],
  };
}

function listLine(list: SourceList): string {
  const rows = `${formatNumber(list.rows)} ${plural(list.rows, 'строка', 'строки', 'строк')}`;
  const kept = typeof list.kept === 'number' ? `, нового ${formatNumber(list.kept)}` : '';
  return `${list.target} — ${rows} за ${formatNumber(list.units)} юн.${kept}`;
}

/** Точка в конце — одна: строка без счёта нового кончается сокращением «юн.». */
function sentence(text: string): string {
  return text.endsWith('.') ? text : `${text}.`;
}

export function CompetitorJournal({ run }: { run: RunCard }) {
  const source = sourceOf(run);
  if (source === null) return null;
  const dropped = Object.entries(source.dropped).filter(([, count]) => count > 0);
  const priced = source.targets.find((list) => list.per_row !== null && list.per_row > 0);
  return (
    <Stack gap={2}>
      <Text size="sm">Списки Ahrefs: {sentence(source.targets.map(listLine).join('; '))}</Text>
      {source.skipped.map((one) => (
        <Text size="sm" key={one.target}>
          Пропущен {one.target}: {one.reason}
        </Text>
      ))}
      {dropped.length > 0 && (
        <Text size="sm" c="dimmed">
          Выброшено до проверки:{' '}
          {dropped
            .map(([code, count]) => `${DROPPED[code] ?? code} ${formatNumber(count)}`)
            .join(', ')}
          .
        </Text>
      )}
      {priced !== undefined && source.row_units > 0 && priced.per_row !== source.row_units && (
        <Text size="sm" c="dimmed">
          Строка списка по ответу Ahrefs — {formatNumber(priced.per_row ?? 0)} юн., по смете —{' '}
          {formatNumber(source.row_units)}: смета считала моделью, её пора перемерить.
        </Text>
      )}
    </Stack>
  );
}
