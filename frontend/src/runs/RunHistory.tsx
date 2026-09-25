/**
 * История прогонов: по странице, ровными колонками, без простыней в ячейках.
 *
 * Замечание 25.09.2026: «пагинация, максимум 10 прогонов на странице;
 * выровнять колонки; убрать большие сообщения, чтобы они не раздували
 * колонку». Три правила, и у каждого своя причина.
 *
 * **Страница считается на сервере и живёт в адресе** (`?page=2`). Срез
 * на экране значил бы грузить всю историю ради десяти строк; номер в адресе
 * переживает обновление и «назад». Размер страницы называет сервер
 * в ответе — своей копии числа у экрана нет.
 *
 * **Ширины колонок заданы, раскладка фиксированная.** При раскладке
 * по содержимому колонка шириной следует за самой длинной строкой
 * страницы, и на второй странице таблица встаёт иначе, чем на первой.
 *
 * **Причина остановки — за значком «!»** (`RunReason`): текст целиком
 * раздувал колонку состояния, и строки вставали разной высоты.
 */

import { Badge, Box, Button, Card, Group, Pagination, Stack, Table, Text } from '@mantine/core';
import { useCallback } from 'react';
import { Link, useLocation, useSearchParams } from 'react-router-dom';

import { countryTitle, RUN_STATUSES } from '../api/labels';
import type { RunCard, RunsView, RunStatus } from '../api/types';
import { formatDateTime, formatNumber } from '../format';
import { RunReason } from './RunReason';

/** Состояния, в которых прогон ещё не кончился: пока такой есть,
 *  список обновляется сам. */
export const ACTIVE = new Set<RunStatus>(['queued', 'estimating', 'running']);

/** Колонки и их доли, сумма — сто процентов. Доли посчитаны по самому
 *  широкому, что бывает в колонке, а не по тому, что есть сейчас:
 *  «Саудовская Аравия», «считает смету» с «!», «исключено 123»,
 *  «расходится 123 из 400 · 31%», «принято 120 · отклонено 340».
 *  Уже `MIN_WIDTH` таблица не ужимается, а уезжает в прокрутку: значок
 *  «закончен» сжимался до «законч…», а «человек не смотрел» вставал
 *  по слову в строку. */
const COLUMNS: { title: string; width: string }[] = [
  { title: 'Прогон', width: '12.2%' },
  { title: 'Состояние', width: '14.4%' },
  { title: 'Ключей', width: '7%' },
  { title: 'Доменов', width: '9.7%' },
  { title: 'Смета', width: '6.5%' },
  { title: 'Факт', width: '6.5%' },
  { title: 'Расхождение', width: '10.8%' },
  { title: 'Судья', width: '16.5%' },
  { title: 'Рассмотрение', width: '16.4%' },
];

/** Меньше — прокрутка. Подобрано так, чтобы на ноутбуке в 1366 пикселей
 *  таблица вставала целиком, а самое длинное из возможного всё ещё
 *  помещалось в ячейку, пусть и заходя на её поля. */
const MIN_WIDTH = 1060;

/** Место под «!» — у каждой строки, с причиной или без. Иначе значки
 *  состояния в строках с причиной сдвигались бы влево на ширину «!»
 *  и стояли бы лесенкой. Ширина — как у кнопки `ActionIcon` размера md. */
const HINT_SLOT = 28;

/** Стрелки переключателя — значки без текста; имя им даёт подпись. */
const CONTROL_NAMES: Record<string, string> = {
  previous: 'Предыдущая страница',
  next: 'Следующая страница',
  first: 'Первая страница',
  last: 'Последняя страница',
};

/** Номер страницы истории — из адреса. Негодный или пустой — первая:
 *  ссылка с опечаткой в номере не должна ронять экран. */
export function useHistoryPage(): [number, (next: number, replace?: boolean) => void] {
  const [params, setParams] = useSearchParams();
  const asked = Number(params.get('page'));
  const page = Number.isInteger(asked) && asked >= 1 ? asked : 1;
  const goTo = useCallback(
    (next: number, replace = false) => {
      // Переход на ту же страницу — не переход: лишняя запись в истории
      // заставила бы нажимать «назад» дважды.
      if (next === page) return;
      setParams(
        (was) => {
          const moved = new URLSearchParams(was);
          // Первая страница — без номера: адрес экрана тот же, что в меню.
          if (next <= 1) moved.delete('page');
          else moved.set('page', String(next));
          return moved;
        },
        { replace },
      );
    },
    [page, setParams],
  );
  return [page, goTo];
}

/** Сколько страниц — по ответу сервера. Пока ответа нет — одна. */
export function pagesOf(view: RunsView | null): number {
  return view === null ? 1 : Math.max(1, Math.ceil(view.total / view.limit));
}

/** Сколько прошло с последней отметки о жизни. У идущего прогона это
 *  удар heartbeat: по времени последней записи медленный прогон
 *  неотличим от мёртвого, а по отметке — отличим. */
function aliveFor(moment: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(moment).getTime()) / 1000));
  if (seconds < 90) return `${seconds} с назад`;
  return `${Math.round(seconds / 60)} мин назад`;
}

/** Сколько отрезал бы судья — из ветки отчёта. `null` — судья был выключен:
 *  ноль читался бы как «судил и никого не нашёл», а это другая новость. */
function judgeCut(run: { stats: Record<string, unknown> | null }): number | null {
  const judge = run.stats?.['judge'];
  if (typeof judge !== 'object' || judge === null) return null;
  const cut = (judge as Record<string, unknown>)['would_cut'];
  return typeof cut === 'number' ? cut : null;
}

/** Модель не ответила судье: у этих доменов вердикта нет, следующий прогон
 *  судит их снова. Отдельной меткой, а не нулём в «отрезал бы»: прогон №21
 *  без ключа модели показывал «отрезал бы 0», и неработающий судья выглядел
 *  как судья, никого не отрезавший. Причина — в подсказке: что чинить. */
function JudgeSilence({ run }: { run: { stats: Record<string, unknown> | null } }) {
  const judge = run.stats?.['judge'];
  if (typeof judge !== 'object' || judge === null) return null;
  const { unanswered, unanswered_reason: reason } = judge as Record<string, unknown>;
  if (typeof unanswered !== 'number' || unanswered === 0) return null;
  return (
    <Badge
      variant="light"
      size="sm"
      color="red"
      title={typeof reason === 'string' ? reason : undefined}
    >
      модель не ответила: {unanswered}
    </Badge>
  );
}

/** Очередь рассмотрения прогона: сколько ждёт решения и ссылка к нему.
 *  Прогон кончается очередью, а не базой, — без этой ячейки её не найти.
 *  Ссылка несёт страницу истории (`from`): «К прогонам» в карточке ведёт
 *  на ту же страницу, а не на первую. */
function ReviewCell({ run, from }: { run: RunCard; from: string }) {
  const pending = run.queue.pending ?? 0;
  const accepted = run.queue.accepted ?? 0;
  const rejected = run.queue.rejected ?? 0;
  if (pending + accepted + rejected === 0) {
    return (
      <Text size="xs" c="dimmed">
        очереди нет
      </Text>
    );
  }
  return (
    <Stack gap={4} align="center">
      <Button
        component={Link}
        to={`/runs/${run.id}/review`}
        state={{ from }}
        size="compact-sm"
        variant={pending > 0 ? 'filled' : 'default'}
      >
        {pending > 0 ? `Рассмотреть ${pending}` : 'Открыть'}
      </Button>
      <Text size="xs" c="dimmed">
        принято {accepted} · отклонено {rejected}
      </Text>
    </Stack>
  );
}

function excludedIn(run: { stats: Record<string, unknown> | null }): number {
  const value = run.stats?.['excluded'];
  return typeof value === 'number' ? value : 0;
}

function HistoryRow({ run, from }: { run: RunCard; from: string }) {
  return (
    <Table.Tr>
      <Table.Td>
        <Text fw={500}>№{run.id}</Text>
        <Text size="xs" c="dimmed">
          {countryTitle(run.country)}
        </Text>
        <Text size="xs" c="dimmed">
          {formatDateTime(run.started_at)}
        </Text>
      </Table.Td>
      <Table.Td>
        <Stack gap={4} align="center">
          <Group justify="center" gap={4} wrap="nowrap">
            <Badge variant="light" color={RUN_STATUSES[run.status].color} style={{ flexShrink: 0 }}>
              {RUN_STATUSES[run.status].title}
            </Badge>
            {/* Значок только там, где есть что сказать: пустой «!» учил бы
                человека нажимать и ничего не находить. Место под него
                держится всегда — пустым местом, а не спрятанным значком. */}
            {run.reason !== null ? (
              <RunReason reason={run.reason} status={run.status} />
            ) : (
              <Box w={HINT_SLOT} style={{ flexShrink: 0 }} aria-hidden="true" />
            )}
          </Group>
          {ACTIVE.has(run.status) && (
            <Text size="xs" c="dimmed">
              {aliveFor(run.alive_at)}
            </Text>
          )}
        </Stack>
      </Table.Td>
      <Table.Td>{formatNumber(run.keywords)}</Table.Td>
      <Table.Td>
        {formatNumber(run.hosts)}
        {excludedIn(run) ? (
          <Text size="xs" c="dimmed">
            исключено {excludedIn(run)}
          </Text>
        ) : null}
      </Table.Td>
      <Table.Td>{formatNumber(run.estimated_units)}</Table.Td>
      <Table.Td>{formatNumber(run.actual_units)}</Table.Td>
      <Table.Td>
        {/* Расхождение сметы и факта — единственная проверка сметы.
            Без неё оценка расхода ничем не подтверждается. */}
        {run.estimate_error === null
          ? '—'
          : `${run.estimate_error > 0 ? '+' : ''}${(run.estimate_error * 100).toFixed(0)}%`}
      </Table.Td>
      <Table.Td>
        {/* Доля расхождений человека с судьёй — единственная проверка
            судьи, как расхождение сметы и факта — проверка сметы. */}
        {judgeCut(run) === null ? (
          <Text size="xs" c="dimmed">
            выключен
          </Text>
        ) : (
          <Text size="sm">отрезал бы {judgeCut(run)}</Text>
        )}
        <JudgeSilence run={run} />
        {/* Цвет несёт значок, а не текст: янтарь — «нужно внимание»,
            и на подложке значка чернила держат норму контраста. */}
        {run.reviewed === 0 ? (
          <Text size="xs" c="dimmed">
            человек не смотрел
          </Text>
        ) : (
          <Badge variant="light" size="sm" color={run.disagreements > 0 ? 'yellow' : 'green'}>
            расходится {run.disagreements} из {run.reviewed} ·{' '}
            {Math.round((run.disagreements / run.reviewed) * 100)}%
          </Badge>
        )}
      </Table.Td>
      <Table.Td>
        <ReviewCell run={run} from={from} />
      </Table.Td>
    </Table.Tr>
  );
}

interface Props {
  view: RunsView | null;
  page: number;
  onPage: (next: number) => void;
}

export function RunHistory({ view, page, onPage }: Props) {
  const rows = view?.runs ?? [];
  const pages = pagesOf(view);
  const { search } = useLocation();

  return (
    <Card className="glass" p="xs">
      {/* Таблица шире телефона — на узком окне уезжает в прокрутку, а не
          ужимает колонки. */}
      <Table.ScrollContainer minWidth={MIN_WIDTH}>
        <Table
          className="dataTable runsTable"
          layout="fixed"
          verticalSpacing="sm"
          horizontalSpacing="xs"
        >
          <colgroup>
            {COLUMNS.map((column) => (
              <col key={column.title} style={{ width: column.width }} />
            ))}
          </colgroup>
          <Table.Thead>
            <Table.Tr>
              {COLUMNS.map((column) => (
                <Table.Th key={column.title}>{column.title}</Table.Th>
              ))}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((run) => (
              <HistoryRow key={run.id} run={run} from={search} />
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {view !== null && view.total === 0 && (
        <Text size="sm" c="dimmed" p="lg">
          Прогонов ещё не было. Первый появится здесь сразу после запуска — вместе со сметой, с
          которой его потом сравнят.
        </Text>
      )}
      {/* Одна страница — переключателю нечего переключать, и его нет вовсе:
          он не отрисовывается, а не прячется атрибутом. */}
      {pages > 1 && (
        <Box component="nav" aria-label="Страницы истории прогонов" pt="xs" pb="sm">
          <Group justify="center">
            <Pagination
              value={Math.min(page, pages)}
              onChange={onPage}
              total={pages}
              radius="xl"
              // Номер — в своём элементе: по кругу кнопки замер делит заливку
              // и фон вокруг круга, а не цифру и заливку (1,28 : 1 цифре,
              // которая читается чисто), — мерить надо саму цифру.
              getItemProps={(number) => ({
                'aria-label': `Страница ${number}`,
                children: <span data-page-number>{number}</span>,
              })}
              getControlProps={(control) => ({ 'aria-label': CONTROL_NAMES[control] })}
            />
          </Group>
        </Box>
      )}
    </Card>
  );
}
