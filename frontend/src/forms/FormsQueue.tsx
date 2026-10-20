/**
 * Одна ручная очередь «Форм» — шапка с вкладками и ориентиром темпа, таблица по
 * страницам, окно адреса и «Не вышло» с подтверждением. Чья очередь — доноров или
 * рекламодателей — говорит её описание (`formQueues.tsx`); устройство экрана и его
 * история — в `FormsPage.tsx`.
 */

import {
  Alert,
  Anchor,
  Button,
  Card,
  Group,
  Loader,
  Modal,
  SegmentedControl,
  Stack,
  Table,
  Text,
  TextInput,
} from '@mantine/core';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { refusalOf } from '../api/client';
import { useSession } from '../auth/AuthProvider';
import { ConfirmPopover } from '../components/ConfirmPopover';
import { DataTable } from '../components/DataTable';
import { PageHead } from '../components/PageHead';
import { PageSwitch, usePageParam } from '../components/PageSwitch';
import { Seams } from '../components/Seams';
import { formatNumber, plural } from '../format';
import { notify } from '../notices';
import { FormLetterModal } from './FormLetterModal';
import type { LetterTarget } from './FormLetterModal';
import type { FormQueue, FormsTab, QueueColumn, QueuePage } from './formQueues';

/** Колонка кнопок — 29 долей: «Вписать адрес» и «Не вышло» с зазором и полями ячейки
 *  (260 px) на `TABLE_MIN_WIDTH`; у доноров ещё «Форма и письмо» (20.10.2026) — 42 доли
 *  под три кнопки (`LETTER_ACTIONS`). */
const ACTIONS = { title: 'Действия', share: 29 };
const LETTER_ACTIONS = { title: 'Действия', share: 42 };

/** Уже этого таблица уезжает в прокрутку: с кнопками — их доля не меньше их ширины,
 *  без кнопок — имени его двести. */
const TABLE_MIN_WIDTH = { withActions: 900, withLetter: 1040, readOnly: 650 } as const;

const TAB_TITLES: Record<FormsTab, string> = { donors: 'Доноры', advertisers: 'Рекламодатели' };

function tabLabel(tab: FormsTab, count: number | undefined): string {
  return count === undefined ? TAB_TITLES[tab] : `${TAB_TITLES[tab]} · ${formatNumber(count)}`;
}

/** Вкладки очередей с числами: своё — из ответа, соседнее — из него же. */
function Tabs({
  tab,
  own,
  other,
  onTab,
}: {
  tab: FormsTab;
  own: number | undefined;
  other: number | undefined;
  onTab: (tab: FormsTab) => void;
}) {
  const counts: Record<FormsTab, number | undefined> =
    tab === 'donors' ? { donors: own, advertisers: other } : { donors: other, advertisers: own };
  return (
    <SegmentedControl
      aria-label="Чья очередь форм"
      value={tab}
      onChange={(picked) => onTab(picked === 'advertisers' ? 'advertisers' : 'donors')}
      data={(['donors', 'advertisers'] as const).map((key) => ({
        value: key,
        label: tabLabel(key, counts[key]),
      }))}
      style={{ alignSelf: 'flex-start' }}
    />
  );
}

/** Остаток ориентира темпа — строкой; дошёл до нуля — что это не запрет. */
function Pace({ total, pace, left }: { total: number; pace: number; left: number }) {
  return (
    <>
      <Text size="sm">
        В очереди <b>{formatNumber(total)}</b>. Ориентир темпа — {formatNumber(pace)}{' '}
        {plural(pace, 'адрес', 'адреса', 'адресов')} руками за последние 30 дней, осталось{' '}
        <b>{formatNumber(left)}</b>.
      </Text>
      {left === 0 && (
        <Alert color="yellow" title="Ориентир темпа достигнут">
          Это ориентир, а не запрет: очередь не закрыта, новые формы в неё попадают, вписывать
          адреса можно и дальше.
        </Alert>
      )}
    </>
  );
}

/** Страница очереди с сервера; страница за концом — переход на последнюю. */
function useQueuePage<Row, Page extends QueuePage<Row>>(queue: FormQueue<Row, Page>) {
  const [page, goToPage] = usePageParam();
  const query = useQuery({
    queryKey: [...queue.queryKey, page],
    queryFn: () => queue.fetch(page),
    // Пока едет следующая страница, стоит прежняя: пустая таблица на долю
    // секунды читалась бы как «очередь пуста».
    placeholderData: keepPreviousData,
  });
  const data = query.data;
  const pages = data === undefined ? 1 : Math.max(1, Math.ceil(data.total / data.limit));
  const settled = data !== undefined && !query.isPlaceholderData;
  // Страница за концом — ссылка, открытая после разбора очереди, или последняя
  // строка последней страницы, закрытая только что: экран уходит на последнюю,
  // заменяя адрес, а не добавляя.
  useEffect(() => {
    if (settled && page > pages) goToPage(pages, true);
  }, [settled, page, pages, goToPage]);
  return { query, page, pages, goToPage };
}

/** «Форму заполнили» и «Не вышло» — перечитывают обе очереди: число соседней
 *  вкладки и ориентир темпа общие. */
function useActions<Row, Page extends QueuePage<Row>>(
  queue: FormQueue<Row, Page>,
  done: () => void,
) {
  const queryClient = useQueryClient();
  const finish = async (message: string) => {
    await queryClient.invalidateQueries({ queryKey: ['forms'] });
    done();
    notify({ message, color: 'green' });
  };
  const fill = useMutation({
    mutationFn: ({ row, email }: { row: Row; email: string }) => queue.fill(row, email),
    onSuccess: (row) => finish(queue.filled(queue.hostOf(row))),
    onError: (failure) =>
      notify({ title: 'Не записали', message: refusalOf(failure), color: 'red' }),
  });
  const giveUp = useMutation({
    mutationFn: (row: Row) => queue.giveUp(row),
    onSuccess: (row) => finish(`${queue.hostOf(row)} закрыт без адреса`),
    onError: (failure) =>
      notify({ title: 'Не закрыли', message: refusalOf(failure), color: 'red' }),
  });
  return { fill, giveUp };
}

function Name({ host, formUrl }: { host: string; formUrl: string | null }) {
  // Имя — чернилами, как у доноров и диалогов: бирюзовая ссылка в верхних строках
  // стоит на бирюзовом углу полотна, и замер дал 4,30 : 1 при норме 4,5 (25.09.2026).
  // Длинный домен переносится по швам (`Seams`), а не посреди слова.
  return (
    <Stack gap={2} align="center">
      <Anchor
        href={`https://${host}`}
        target="_blank"
        rel="noreferrer"
        c="var(--ink)"
        fw={500}
        underline="hover"
        className="cellName"
      >
        <Seams text={host} />
      </Anchor>
      {/* Страница формы — прямо, если лестница её запомнила (с 20.10.2026). */}
      {formUrl !== null && (
        <Anchor href={formUrl} target="_blank" rel="noreferrer" size="xs" c="var(--ink)">
          открыть форму
        </Anchor>
      )}
    </Stack>
  );
}

interface RowActions<Row> {
  stale: boolean;
  closing: number | null;
  onFill: (row: Row) => void;
  onGiveUp: (row: Row) => void;
  /** «Форма и письмо» — у очереди, где собирается текст письма (доноры). */
  onLetter: ((row: Row) => void) | null;
}

function Actions<Row, Page extends QueuePage<Row>>({
  queue,
  page,
  row,
  actions,
}: {
  queue: FormQueue<Row, Page>;
  page: Page;
  row: Row;
  actions: RowActions<Row>;
}) {
  return (
    <Table.Td className="cellDecide">
      {/* Переносом: на телефоне три кнопки в ряд не помещаются и резались до «Форма и
          пись» (снимок 20.10.2026); на широком окне колонка мерена под три в ряд. */}
      <Group gap="xs" justify="center" wrap="wrap">
        {actions.onLetter !== null && (
          <Button
            size="compact-sm"
            variant="default"
            className="press"
            disabled={actions.stale}
            onClick={() => actions.onLetter?.(row)}
          >
            Форма и письмо
          </Button>
        )}
        <Button
          size="compact-sm"
          className="press"
          disabled={actions.stale}
          onClick={() => actions.onFill(row)}
        >
          Вписать адрес
        </Button>
        {/* Закрывает строку насовсем — с подтверждением у кнопки: стоит вплотную
            к «Вписать адрес», и промах стоил сайта (аудит экранов 09.10.2026). */}
        <ConfirmPopover
          message={queue.closeAsk(queue.hostOf(row), page)}
          confirm="Закрыть"
          onConfirm={() => actions.onGiveUp(row)}
        >
          {(ask) => (
            <Button
              size="compact-sm"
              variant="subtle"
              color="gray"
              className="press"
              disabled={actions.stale}
              loading={actions.closing === queue.idOf(row)}
              onClick={ask}
            >
              Не вышло
            </Button>
          )}
        </ConfirmPopover>
      </Group>
    </Table.Td>
  );
}

function QueueTable<Row, Page extends QueuePage<Row>>({
  queue,
  page,
  actions,
}: {
  queue: FormQueue<Row, Page>;
  page: Page;
  actions: RowActions<Row> | null;
}) {
  const shared: { title: string; share: number }[] = [queue.name, ...queue.columns];
  const letter = actions?.onLetter != null;
  if (actions !== null) shared.push(letter ? LETTER_ACTIONS : ACTIONS);
  const shares = shared.reduce((sum, column) => sum + column.share, 0);
  const columns = shared.map((column) => ({
    title: column.title,
    width: `${(column.share / shares) * 100}%`,
  }));
  // Строки прежней страницы, пока едет новая, приглушены, и кнопки в них
  // заперты: решать по ним нельзя — они не той страницы.
  return (
    <DataTable
      columns={columns}
      minWidth={
        actions === null
          ? TABLE_MIN_WIDTH.readOnly
          : letter
            ? TABLE_MIN_WIDTH.withLetter
            : TABLE_MIN_WIDTH.withActions
      }
      className="allCentered"
      cardRow
      stale={actions?.stale ?? false}
    >
      {page.rows.map((row) => (
        <Table.Tr key={queue.idOf(row)}>
          <Table.Td>
            <Name host={queue.hostOf(row)} formUrl={queue.formOf(row)} />
          </Table.Td>
          {queue.columns.map((column: QueueColumn<Row>) => (
            <Table.Td key={column.title} data-label={column.title}>
              {column.cell(row)}
            </Table.Td>
          ))}
          {actions !== null ? (
            <Actions queue={queue} page={page} row={row} actions={actions} />
          ) : null}
        </Table.Tr>
      ))}
    </DataTable>
  );
}

function AddressModal({
  whose,
  about,
  host,
  email,
  onEmail,
  busy,
  onClose,
  onSave,
}: {
  whose: string;
  about: string;
  host: string | null;
  email: string;
  onEmail: (email: string) => void;
  busy: boolean;
  onClose: () => void;
  onSave: (email: string) => void;
}) {
  return (
    <Modal opened={host !== null} onClose={onClose} title={`Адрес ${whose} ${host ?? ''}`}>
      <Stack gap="sm">
        <Text size="sm">{about}</Text>
        <TextInput
          label="Адрес почты"
          placeholder="editor@site.com"
          value={email}
          onChange={(event) => onEmail(event.currentTarget.value)}
        />
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Отмена
          </Button>
          <Button
            loading={busy}
            disabled={!email.includes('@')}
            onClick={() => onSave(email.trim())}
          >
            Записать
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

export function FormsQueue<Row, Page extends QueuePage<Row>>({
  queue,
  onTab,
}: {
  queue: FormQueue<Row, Page>;
  onTab: (tab: FormsTab) => void;
}) {
  const { can } = useSession();
  const [filling, setFilling] = useState<Row | null>(null);
  const [writing, setWriting] = useState<LetterTarget | null>(null);
  const [email, setEmail] = useState('');
  const { query, page, pages, goToPage } = useQueuePage(queue);
  const { fill, giveUp } = useActions(queue, () => setFilling(null));
  const { data, isLoading, error, isPlaceholderData } = query;

  if (isLoading) return <Loader aria-label="Загружаем очередь форм" m="md" />;
  if (error || data === undefined) {
    return (
      <Alert color="red" title="Очередь не загрузилась" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }
  // Пуста очередь, а не страница: страница за концом — переход на последнюю,
  // а не «очередь пуста» на мгновение перед ним.
  const empty = data.total === 0;
  const actions: RowActions<Row> | null = can('run')
    ? {
        stale: isPlaceholderData,
        closing: giveUp.isPending && giveUp.variables ? queue.idOf(giveUp.variables) : null,
        onFill: (row) => {
          setFilling(row);
          setEmail('');
        },
        onGiveUp: (row) => giveUp.mutate(row),
        onLetter: letterOf(queue, setWriting),
      }
    : null;

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="sm">
          <PageHead title="Формы" hint={queue.hint} />
          <Tabs tab={queue.tab} own={data.total} other={queue.other(data)} onTab={onTab} />
          <Pace total={data.total} pace={data.monthly_cap} left={data.monthly_left} />
        </Stack>
      </Card>

      {/* Поля карточки с таблицей — вместе с полем ячейки те же 32 px, что у
          панели сверху (аудит 25.09.2026). */}
      <Card className="glassPanel" p={empty ? 'xl' : 'md'}>
        {empty ? (
          <Text size="sm" c="dimmed">
            {queue.empty}
          </Text>
        ) : (
          <Stack gap="sm">
            <QueueTable queue={queue} page={data} actions={actions} />
            <PageSwitch
              label="Страницы очереди форм"
              page={page}
              pages={pages}
              onChange={goToPage}
              pt="xs"
            />
          </Stack>
        )}
      </Card>

      <AddressModal
        whose={queue.whose}
        about={queue.about}
        host={filling === null ? null : queue.hostOf(filling)}
        email={email}
        onEmail={setEmail}
        busy={fill.isPending}
        onClose={() => setFilling(null)}
        onSave={(email) => filling !== null && fill.mutate({ row: filling, email })}
      />
      <FormLetterModal target={writing} onClose={() => setWriting(null)} />
    </Stack>
  );
}

/** «Форма и письмо» — открыть окно строки, если у очереди собирается текст письма. */
function letterOf<Row, Page extends QueuePage<Row>>(
  queue: FormQueue<Row, Page>,
  open: (target: LetterTarget) => void,
): ((row: Row) => void) | null {
  const donor = queue.letterOf;
  if (donor === undefined) return null;
  return (row) => open({ donorId: donor(row), host: queue.hostOf(row) });
}
