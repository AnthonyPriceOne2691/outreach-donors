/**
 * Записи журнала таблицей: когда, кто, что, над чем, подробности — словами
 * (`api/auditLabels.ts`), а не кодами, которыми журнал хранится.
 *
 * **Колонки — по самому длинному, что в них бывает**, раскладка фиксированная; замер
 * холстом шрифтом экрана (14 px, 12.10.2026): дата — 122 px, почта учётки до 200 px (около
 * 27 знаков) — в одну строку. Под «Что» и «Над чем» названия длиннее 192 и 184 px — «Цепочка
 * писем продаж изменена», «кандидат в рекламодатели №12345» (до 244) — встают в две
 * строки; в прежних 15rem — длиннее 208. Подробностям — остаток, они переносятся.
 *
 * **Влезает рядом с развёрнутым меню на 1440** (просьба Anthony 12.10.2026): там под
 * таблицу 1 078 px, а при прежних 1 180 она уезжала в прокрутку, и подробности по
 * центру уходили за край. Уже `JOURNAL_MIN_WIDTH` — прокрутка, как прежде.
 *
 * **Подробности — по центру, как прочие колонки**, и от «Над чем» их отделяет двойной
 * зазор (`journalTable` в glass.css; просьба Anthony 12.10.2026).
 *
 * **«Действие» подробностей — под названием**, а не строкой среди прочих: у общих
 * «Правки» и «Запуска» именно оно говорит, что случилось.
 *
 * **Подробности — итог одной строкой, остальное раскрытием строки** («+N ещё» и «Свернуть» на
 * том же месте; что на виду — `journalDetails.ts`). Проверка прода 10.11.2026: шесть-семь строк
 * подробностей у каждого письма растили страницу журнала до 7 016 px. Свёрнутое ищется поиском
 * браузера (`hidden="until-found"`, правило 10.11.2026): адрес и домен в нём — данные, а не
 * настройка за переключателем, и найденная строка раскрывается сама.
 *
 * **Шапка закреплена** (`stickyTable`, как у `DataTable sticky`): строки прокручиваются под ней
 * в высоту окна. Смена страницы или фильтра открывает таблицу сначала — её перерисовывает экран
 * (`key` в `JournalPage`).
 *
 * **Почта и домен переносятся по швам** (`Seams`): почта учётки в 14,5rem рвалась на три
 * строки по дефису и посреди зоны — в последней оставалась одна буква.
 */

import { Anchor, Button, Group, Table, Text } from '@mantine/core';
import { IconChevronDown, IconChevronUp } from '@tabler/icons-react';
import { Fragment, useCallback, useId, useLayoutEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

import { actionTitle, targetParts } from '../api/auditLabels';
import type { DetailLine } from '../api/auditLabels';
import type { JournalEntry } from '../api/journal';
import { ColumnsHead } from '../components/ColumnsHead';
import type { Column } from '../components/ColumnsHead';
import { Seams } from '../components/Seams';
import { formatDateTime } from '../format';
import { detailView } from './journalDetails';

const COLUMNS: Column[] = [
  { title: 'Когда', width: '10rem' },
  { title: 'Кто', width: '14.5rem' },
  { title: 'Что', width: '14rem' },
  { title: 'Над чем', width: '13.5rem' },
  { title: 'Подробности' },
];

/** Меньше — прокрутка. Колонкам с шириной — 832 px, подробностям — не меньше 208 (текста
 *  160: строка «ключ: значение» — в две-три строки). Запас до 1 078 — полоса прокрутки
 *  страницы там, где она занимает место (Windows, мышь на маке), и шрифт шире SF. */
export const JOURNAL_MIN_WIDTH = 1040;

/** Значение подробности: почта — по швам почты (после «@»), домен — по швам домена, прочее —
 *  как есть. Без швов адрес «от кого» рвался посреди имени домена. */
const ADDRESS = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const HOST = /^[^\s@/]+\.[a-z]{2,}$/i;

function Value({ text }: { text: string }) {
  if (ADDRESS.test(text)) return <Seams text={text} address />;
  if (HOST.test(text)) return <Seams text={text} />;
  return <>{text}</>;
}

/** Строка подробностей: пары «ключ: значение» через точку; ключ — приглушённо. `children` —
 *  продолжение строки: кнопка «+N ещё» у итога. */
function DetailText({ lines, children }: { lines: DetailLine[]; children?: ReactNode }) {
  return (
    <Text size="xs" className="journalDetail">
      {lines.map((line, index) => (
        <Fragment key={line.label}>
          {index > 0 && (
            <Text span size="xs" c="dimmed">
              {' · '}
            </Text>
          )}
          <Text span size="xs" c="dimmed">
            {line.label}:
          </Text>{' '}
          <Value text={line.value} />
        </Fragment>
      ))}
      {children}
    </Text>
  );
}

interface ToggleProps {
  /** Сколько строк в раскрытии. */
  count: number;
  open: boolean;
  /** Чем кнопка управляет — раскрытое под итогом. */
  restId: string;
  onToggle: () => void;
}

/** «+N ещё» и «Свернуть» — одна кнопка на одном месте: раскрытое встаёт под итогом. */
function MoreToggle({ count, open, restId, onToggle }: ToggleProps) {
  const Chevron = open ? IconChevronUp : IconChevronDown;
  return (
    <Button
      variant="subtle"
      size="compact-xs"
      className="press journalMore"
      aria-expanded={open}
      aria-controls={restId}
      rightSection={<Chevron size={12} />}
      onClick={onToggle}
    >
      {open ? 'Свернуть' : `+${count}\u00a0ещё`}
    </Button>
  );
}

interface FoldedProps {
  id: string;
  open: boolean;
  /** Поиск браузера нашёл текст в свёрнутом — строка раскрывается. */
  onFound: () => void;
  children: ReactNode;
}

/** Свёрнутые строки — в документе, но скрыты «до находки» (`hidden="until-found"`): поиск
 *  браузера находит в них адрес и раскрывает сам, событием `beforematch`. React пишет `hidden`
 *  только пустым (булев атрибут), поэтому значение ставится здесь. Браузер без «до находки»
 *  читает атрибут как обычный `hidden` — свёрнутое просто скрыто. */
function Folded({ id, open, onFound, children }: FoldedProps) {
  const box = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const node = box.current;
    if (node === null) return undefined;
    if (open) {
      node.removeAttribute('hidden');
      return undefined;
    }
    node.setAttribute('hidden', 'until-found');
    node.addEventListener('beforematch', onFound);
    return () => node.removeEventListener('beforematch', onFound);
  }, [open, onFound]);
  return (
    <div id={id} ref={box}>
      {children}
    </div>
  );
}

/** Подробности записи: отклонение и итог — на виду, остальное — «+N ещё» продолжением итога. */
function Details({ entry, skip }: { entry: JournalEntry; skip: ReadonlySet<string> }) {
  const [open, setOpen] = useState(false);
  const found = useCallback(() => setOpen(true), []);
  const restId = useId();
  const { alerts, face, more, folded } = detailView(entry.details, skip);
  if (alerts.length + face.length + more.length + folded.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        —
      </Text>
    );
  }
  const toggle = folded.length > 0 && (
    <MoreToggle
      count={folded.length}
      open={open}
      restId={restId}
      onToggle={() => setOpen((was) => !was)}
    />
  );
  return (
    <>
      {alerts.map((line) => (
        <DetailText key={line.label} lines={[line]} />
      ))}
      {face.length > 0 ? (
        <DetailText lines={face}>{toggle && <> {toggle}</>}</DetailText>
      ) : (
        toggle && <Group justify="center">{toggle}</Group>
      )}
      {more.map((line) => (
        <DetailText key={line.label} lines={[line]} />
      ))}
      {folded.length > 0 && (
        <Folded id={restId} open={open} onFound={found}>
          {folded.map((line) => (
            <DetailText key={line.label} lines={[line]} />
          ))}
        </Folded>
      )}
    </>
  );
}

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
            <Seams text={who.said} address />
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
                  <Seams text={part.text} address />
                ) : (
                  <Anchor component={Link} to={part.to} c="var(--ink)" underline="always">
                    <Seams text={part.text} address />
                  </Anchor>
                )}
              </Fragment>
            ))}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        <Details entry={entry} skip={skip} />
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
    <Table.ScrollContainer
      minWidth={JOURNAL_MIN_WIDTH}
      type="native"
      className="scrollSlim stickyTable"
    >
      <Table
        className="dataTable journalTable"
        layout="fixed"
        verticalSpacing="sm"
        horizontalSpacing="md"
        stickyHeader
        aria-label="Записи журнала"
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
