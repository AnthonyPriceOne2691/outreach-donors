/**
 * Превью плана работ в окне загрузки: листы книги и смета диапазоном.
 *
 * По листу — гипотеза, рынки, язык, цель, глубина, ключей взято и отсеяно и смета «от — до»;
 * ошибки и предупреждения — под таблицей, у имени листа. Смета плана — внизу: от первого прохода
 * (все ключи на топ-10) до предельной глубины. Почему план не сохранить — плашкой: пока она есть,
 * «Сохранить план» закрыт (`PlanUpload`).
 */

import { Alert, List, Stack, Table, Text } from '@mantine/core';

import type { PlanPreview, PlanSheet, PlanSpend } from '../api/salesPlans';
import { InfoHint } from '../components/InfoHint';
import { formatNumber, formatUsd } from '../format';

/** Язык писем словами. */
const LANGUAGES: Record<string, string> = { ru: 'русский', en: 'английский' };

/** Глубина страницами — словами книги: «топ-50». */
export function topWords(pages: number): string {
  return `топ-${pages * 10}`;
}

/** Деньги прохода: «0,72 $». */
function usd(spend: PlanSpend | null): string {
  return spend === null ? '—' : formatUsd(spend.usd);
}

/** Смета «от — до»; одна глубина — одно число. */
export function rangeWords(low: PlanSpend | null, high: PlanSpend | null): string {
  if (low === null || high === null) return '—';
  return low.usd === high.usd ? usd(low) : `${usd(low)} — ${usd(high)}`;
}

function SheetRow({ sheet }: { sheet: PlanSheet }) {
  const goal = sheet.goal ?? sheet.target;
  return (
    <Table.Tr>
      <Table.Td>
        <Text size="sm" fw={500}>
          {sheet.name || '—'}
        </Text>
        <Text size="xs" c="dimmed">
          {sheet.sheet}
        </Text>
      </Table.Td>
      <Table.Td>{sheet.markets.map((code) => code.toUpperCase()).join(', ') || '—'}</Table.Td>
      <Table.Td>{LANGUAGES[sheet.language] ?? (sheet.language || '—')}</Table.Td>
      <Table.Td>{goal === null ? '—' : formatNumber(goal)}</Table.Td>
      <Table.Td>{topWords(sheet.depth_pages)}</Table.Td>
      <Table.Td>
        {formatNumber(sheet.keys.taken)}
        {sheet.keys.rejected > 0 ? ` · отсеяно ${formatNumber(sheet.keys.rejected)}` : ''}
      </Table.Td>
      <Table.Td>{rangeWords(sheet.low, sheet.high)}</Table.Td>
    </Table.Tr>
  );
}

/** Ошибки (красным) и предупреждения листов — у имени листа. */
function SheetNotes({ sheets }: { sheets: PlanSheet[] }) {
  const noted = sheets.filter((sheet) => sheet.issues.length + sheet.warnings.length > 0);
  if (noted.length === 0) return null;
  return (
    <Stack gap={6} aria-label="Ошибки и предупреждения листов">
      {noted.map((sheet) => (
        <div key={sheet.sheet}>
          <Text size="sm" fw={500}>
            Лист «{sheet.sheet}»
          </Text>
          <List size="sm" spacing={2}>
            {sheet.issues.map((issue) => (
              <List.Item key={issue.words}>
                <Text size="sm" c="red">
                  {issue.words}
                </Text>
              </List.Item>
            ))}
            {sheet.warnings.map((words) => (
              <List.Item key={words}>
                <Text size="sm" c="dimmed">
                  {words}
                </Text>
              </List.Item>
            ))}
          </List>
        </div>
      ))}
    </Stack>
  );
}

/** Почему диапазон и чего в нём нет — у «i» сметы плана. */
function EstimateHint() {
  return (
    <InfoHint name="Как считается смета" width={340}>
      От — все ключи на топ-10. До — все ключи до «Глубины поиска до» листа. Деньги — выдача и
      модель; Hunter считается запросами. Листы с ошибками в смету не входят.
    </InfoHint>
  );
}

export function PlanPreviewView({ preview }: { preview: PlanPreview }) {
  const skipped = Object.keys(preview.skipped);
  return (
    <Stack gap="sm">
      <Table.ScrollContainer minWidth={720}>
        <Table
          className="dataTable"
          verticalSpacing="xs"
          horizontalSpacing="sm"
          fz="sm"
          aria-label="Листы книги"
        >
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Гипотеза</Table.Th>
              <Table.Th>Рынки</Table.Th>
              <Table.Th>Язык</Table.Th>
              <Table.Th>Цель</Table.Th>
              <Table.Th>Глубина</Table.Th>
              <Table.Th>Ключей</Table.Th>
              <Table.Th>Смета</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {preview.sheets.map((sheet) => (
              <SheetRow key={sheet.sheet} sheet={sheet} />
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {skipped.length > 0 ? (
        <Text size="xs" c="dimmed">
          Пропущены: {skipped.map((name) => `«${name}» — ${preview.skipped[name]}`).join('; ')}.
        </Text>
      ) : null}
      <SheetNotes sheets={preview.sheets} />
      <Text size="sm" fw={500} aria-label="Смета плана">
        Смета плана: {rangeWords(preview.low, preview.high)} <EstimateHint />
      </Text>
      {preview.notes.map((words) => (
        <Text key={words} size="sm" c="dimmed">
          {words}
        </Text>
      ))}
      {preview.refusals.length > 0 ? (
        <Alert color="red" title="План не сохранить">
          <List size="sm" spacing={2}>
            {preview.refusals.map((words) => (
              <List.Item key={words}>{words}</List.Item>
            ))}
          </List>
        </Alert>
      ) : null}
    </Stack>
  );
}
