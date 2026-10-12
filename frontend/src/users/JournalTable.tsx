/**
 * Записи журнала таблицей: когда, кто, что, над чем, подробности — словами
 * (`api/auditLabels.ts`), а не кодами, которыми журнал хранится.
 *
 * **Колонки — по самому длинному, что в них бывает**, раскладка фиксированная:
 * «23.09.2026, 14:05», почта учётки, «Решение по черновику агента» под «Что»,
 * «запись базы знаний продаж №123» под «Над чем»; подробностям — остаток. Уже
 * `MIN_WIDTH` таблица уезжает в прокрутку, а не ужимает колонки.
 *
 * **Подробности — по центру, как прочие колонки**, и от «Над чем» их отделяет двойной
 * зазор (`journalTable` в glass.css; просьба Anthony 12.10.2026).
 *
 * **«Действие» подробностей — под названием**, а не строкой среди прочих: у общих
 * «Правки» и «Запуска» именно оно говорит, что случилось.
 */

import { Anchor, Table, Text } from '@mantine/core';
import { Fragment } from 'react';
import { Link } from 'react-router-dom';

import { actionTitle, detailLines, targetParts } from '../api/auditLabels';
import type { JournalEntry } from '../api/journal';
import { ColumnsHead } from '../components/ColumnsHead';
import type { Column } from '../components/ColumnsHead';
import { formatDateTime } from '../format';

const COLUMNS: Column[] = [
  { title: 'Когда', width: '10.5rem' },
  { title: 'Кто', width: '15rem' },
  { title: 'Что', width: '15rem' },
  { title: 'Над чем', width: '15rem' },
  { title: 'Подробности' },
];

/** Меньше — прокрутка: подробностям остаётся не меньше 280 px. */
const MIN_WIDTH = 1180;

/** Кто: почта учётки, а без автора — кто назван в подробностях (загрузка из консоли). */
function whoOf(entry: JournalEntry): { said: string | null; fromDetails: boolean } {
  if (entry.author_email !== null) return { said: entry.author_email, fromDetails: false };
  const named = entry.details?.['кто'];
  if (typeof named === 'string' && named !== '') return { said: named, fromDetails: true };
  return { said: null, fromDetails: false };
}

function Row({ entry, emails }: { entry: JournalEntry; emails: ReadonlyMap<number, string> }) {
  const who = whoOf(entry);
  const what = entry.details?.['действие'];
  const skip = new Set(['действие', ...(who.fromDetails ? ['кто'] : [])]);
  const lines = detailLines(entry.details, skip);
  const target = targetParts(entry.target, emails);
  return (
    <Table.Tr>
      <Table.Td>
        <Text size="sm">{formatDateTime(entry.created_at)}</Text>
      </Table.Td>
      <Table.Td>
        {who.said === null ? (
          <Text size="sm" c="dimmed">
            без автора
          </Text>
        ) : (
          <Text size="sm" className="cellName">
            {who.said}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        <Text size="sm" fw={500}>
          {actionTitle(entry.action, entry.target)}
        </Text>
        {typeof what === 'string' && what !== '' && (
          <Text size="xs" c="dimmed">
            {what}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        {target.length === 0 ? (
          <Text size="sm" c="dimmed">
            —
          </Text>
        ) : (
          <Text size="sm">
            {target.map((part, index) => (
              <Fragment key={`${part.text}-${index}`}>
                {index > 0 && ', '}
                {part.to === undefined ? (
                  part.text
                ) : (
                  <Anchor component={Link} to={part.to} c="var(--ink)" underline="always">
                    {part.text}
                  </Anchor>
                )}
              </Fragment>
            ))}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        {lines.length === 0 ? (
          <Text size="sm" c="dimmed">
            —
          </Text>
        ) : (
          lines.map((line) => (
            <Text key={line.label} size="xs" lineClamp={3} className="journalDetail">
              <Text span size="xs" c="dimmed">
                {line.label}:
              </Text>{' '}
              {line.value}
            </Text>
          ))
        )}
      </Table.Td>
    </Table.Tr>
  );
}

interface Props {
  entries: JournalEntry[];
  /** Почта учётки по номеру: цель «user:3» — «учётка ivan@…». */
  emails: ReadonlyMap<number, string>;
  /** Строки прежнего ответа, пока едет новый: приглушены, решать по ним нельзя. */
  stale: boolean;
}

export function JournalTable({ entries, emails, stale }: Props) {
  return (
    <Table.ScrollContainer minWidth={MIN_WIDTH} type="native" className="scrollSlim">
      <Table
        className="dataTable journalTable"
        layout="fixed"
        verticalSpacing="sm"
        horizontalSpacing="md"
      >
        <ColumnsHead columns={COLUMNS} />
        <Table.Tbody className="staleRows" data-stale={stale || undefined}>
          {entries.map((entry) => (
            <Row key={entry.id} entry={entry} emails={emails} />
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
