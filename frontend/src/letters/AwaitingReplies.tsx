/**
 * «Ждём ответа» на экране «Письма» (16.11.2026): переписки этапа, где ответа ещё нет.
 *
 * «Диалоги» доноров и рекламодателей показывают только переписки с ответом, остальные — здесь,
 * рядом с письмами, которые их начали. Диалог или письмо — решает сервер одним полем карточки
 * (`ThreadCard.answered`, `outreach/threads.is_answered`): оба экрана судят одинаково, своего
 * правила у экрана нет. Ответил человек — переписка уходит в «Диалоги» сама.
 *
 * **Свёрнут в строку с числом** (`FoldCard`), как сборка очереди: работа экрана — очередь, сюда
 * заглядывают найти письмо, на которое не ответили. Пусто — строка и почему, раскрывать нечего.
 * Не загрузился — отказ словами, как у соседних блоков.
 *
 * **Таблица — как у соседних экранов** (`DataTable`): домен слева, остальное по центру; на
 * телефоне строка — карточкой с подписями колонок, вбок ничего не уезжает. Домен — ссылка на
 * переписку. Состояние — той же меткой и цветом, что в «Диалогах» (`threadState`), время — по
 * часам смотрящего (`formatDateTime`). Сверху — недавние: последнее событие, новые первыми;
 * письма в очереди событий не имеют и стоят ниже.
 *
 * **Рисуются первые сто строк**, дальше — «Показать ещё 100», как в «Диалогах»: у рекламодателей
 * без ответа — сотни переписок.
 *
 * **Список — этапа целиком**: у рекламодателей один на обе аудитории — аудитории у переписки
 * в карточке нет. Данные — тот же список «Диалогов», суженный сервером (`listAwaitingThreads`).
 */

import { Alert, Badge, Button, Card, Group, Stack, Table, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import type { ReactNode } from 'react';

import { refusalOf } from '../api/client';
import { threadState } from '../api/labels';
import { listAwaitingThreads } from '../api/letters';
import type { LetterStage, ThreadCard } from '../api/types';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { FoldCard } from '../components/FoldCard';
import { InfoHint } from '../components/InfoHint';
import { Seams } from '../components/Seams';
import { formatDateTime, formatNumber } from '../format';
import { ThreadLink } from './ThreadLink';

/** Строк за раз: первая сотня и каждая следующая — как в «Диалогах». */
const AWAITING_STEP = 100;

/** Раз в минуту, как соседние блоки: ответы приходят весь день, и ответившая переписка уходит
 *  отсюда без перезагрузки. */
const REFRESH_MS = 60_000;

/** Ширины — по самому длинному: значок «цепочка остановлена», заголовок «Последнее событие»,
 *  дата «16.11.2026, 09:30». Замер 16.11.2026 в обеих темах: на 1440 таблица — 1 058 px в своей
 *  карточке, значки и заголовки не обрезаны, страница не шире окна; на 390 — карточки, 390 = 390. */
const COLUMNS: Column[] = [
  { title: 'Домен' },
  { title: 'Адрес', width: '13rem' },
  { title: 'Рассылка', width: '9rem' },
  { title: 'Состояние', width: '12rem' },
  { title: 'Писем ушло', width: '7.5rem' },
  { title: 'Последнее событие', width: '11rem' },
];
/** Уже этого таблица уезжает в прокрутку; на телефоне строка — карточкой. */
const TABLE_MIN_WIDTH = 1000;

const TITLE = 'Ждём ответа';

/** Что сюда попадает — в «i» у заголовка. Хвост — свой у этапа: автоответ с суммой ждёт
 *  разбора только у доноров (у рекламодателя сумма — его расход), аудиторий две — у Этапа 2. */
const ABOUT_TAIL: Record<LetterStage, string> = {
  donors:
    'Ответил человек — переписка переходит в «Диалоги»; туда же — автоответ с суммой: цену в нём ' +
    'подтверждает человек.',
  advertisers:
    'Ответил человек — переписка переходит в «Диалоги». Список один на обе аудитории: по ' +
    'найденной ссылке и бизнесам ниши.',
};

function about(stage: LetterStage): string {
  return (
    'Переписки, где ответа человека ещё нет: письмо в очереди или ушло и ждёт ответа, пришёл ' +
    'только автоответ «я в отпуске», отказ доставки или отписка, цепочку остановили руками. ' +
    ABOUT_TAIL[stage]
  );
}

/** Момент последнего события числом; событий нет — в самый низ. */
function eventMoment(thread: ThreadCard): number {
  return thread.last_event_at === null ? -Infinity : Date.parse(thread.last_event_at);
}

/** Переписки без ответа — своего этапа, недавние сверху; при равном — новые переписки. */
export function awaitingOf(threads: ThreadCard[], stage: LetterStage): ThreadCard[] {
  return threads
    .filter((thread) => thread.stage === stage && thread.answered === false)
    .sort((a, b) => eventMoment(b) - eventMoment(a) || b.id - a.id);
}

export function AwaitingReplies({ stage }: { stage: LetterStage }) {
  const [open, setOpen] = useState(false);
  const [asked, setAsked] = useState(AWAITING_STEP);
  const { data, error } = useQuery({
    queryKey: ['letters', 'awaiting', stage],
    queryFn: () => listAwaitingThreads(stage),
    refetchInterval: REFRESH_MS,
  });

  if (data === undefined) {
    return error ? (
      <Alert color="red" title="Переписки без ответа не загрузились">
        {refusalOf(error)}
      </Alert>
    ) : null;
  }
  const threads = awaitingOf(data, stage);
  const hint = (
    <InfoHint name="Что попадает в «Ждём ответа»" width={360}>
      {about(stage)}
    </InfoHint>
  );
  if (threads.length === 0) return <NoneAwaiting hint={hint} />;
  const shown = Math.min(asked, threads.length);

  return (
    <FoldCard
      label={`${TITLE} · ${formatNumber(threads.length)}`}
      open={open}
      onToggle={() => setOpen(!open)}
      aside={hint}
    >
      <Stack gap="sm" pt="sm">
        <DataTable
          columns={COLUMNS}
          minWidth={TABLE_MIN_WIDTH}
          cardRow
          label="Переписки без ответа"
        >
          {threads.slice(0, shown).map((thread) => (
            <AwaitingRow key={thread.id} thread={thread} />
          ))}
        </DataTable>
        <MoreAwaiting
          rest={threads.length - shown}
          onMore={() => setAsked(shown + AWAITING_STEP)}
        />
      </Stack>
    </FoldCard>
  );
}

/** Переписок без ответа нет — строка заголовка и почему, без раскрытия. */
function NoneAwaiting({ hint }: { hint: ReactNode }) {
  return (
    <Card className="glass" px="xl" py="md">
      <Group gap={4} wrap="nowrap">
        <Text fw={500}>{`${TITLE} · 0`}</Text>
        {hint}
      </Group>
      <Text size="sm" c="dimmed">
        Переписок без ответа нет: писем ещё не собирали или на каждое ответил человек — такие
        переписки в «Диалогах».
      </Text>
    </Card>
  );
}

/** Строка: домен — ссылкой на переписку, адрес, рассылка, состояние, писем ушло и когда. */
function AwaitingRow({ thread }: { thread: ThreadCard }) {
  const state = threadState(thread.state);
  return (
    <Table.Tr>
      <Table.Td className="wrapCell cellName">
        <ThreadLink threadId={thread.id}>
          <Seams text={thread.host} />
        </ThreadLink>
      </Table.Td>
      <Table.Td className="wrapCell phoneWide" data-label="Адрес">
        {thread.contact_email === null ? (
          <Text size="sm" c="dimmed">
            адрес не определён
          </Text>
        ) : (
          <Seams text={thread.contact_email} address />
        )}
      </Table.Td>
      <Table.Td className="wrapCell phoneWide" data-label="Рассылка">
        {thread.campaign}
      </Table.Td>
      <Table.Td className="phoneWide" data-label="Состояние">
        <Badge variant="light" color={state.color}>
          {state.title}
        </Badge>
      </Table.Td>
      <Table.Td data-label="Писем ушло">{formatNumber(thread.messages_sent)}</Table.Td>
      <Table.Td data-label="Последнее событие">{formatDateTime(thread.last_event_at)}</Table.Td>
    </Table.Tr>
  );
}

/** «Показать ещё 100» под строками; осталось меньше сотни — кнопка называет остаток. */
function MoreAwaiting({ rest, onMore }: { rest: number; onMore: () => void }) {
  if (rest <= 0) return null;
  const next = Math.min(AWAITING_STEP, rest);
  return (
    <Group gap="sm" justify="center">
      <Button variant="subtle" size="compact-sm" className="press" onClick={onMore}>
        {`Показать ещё\u00a0${formatNumber(next)}`}
      </Button>
      {rest > next ? (
        <Text size="xs" c="dimmed">
          {`осталось\u00a0${formatNumber(rest)}`}
        </Text>
      ) : null}
    </Group>
  );
}
