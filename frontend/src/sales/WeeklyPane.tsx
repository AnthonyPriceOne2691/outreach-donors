/**
 * Недельный отчёт продаж (срез 6.3): восемь разделов формата подрядчика за неделю с понедельника
 * по воскресенье по Москве — контакты, отправки, доставляемость, ответы, диалоги, лиды поимённо,
 * проблемы, план, — числа по гипотезам и выгрузка CSV.
 *
 * **Экран ничего не считает сам.** Разделы приходят словами — теми же строками, что уходят в
 * группу продаж в Telegram (`GET /sales/weekly`), числа по гипотезам и итог считает сервер
 * функциями экранов: воронки, гипотез, очереди. CSV — те же числа строками таблицы. Отчёт,
 * сообщение в группу и выгрузка не расходятся.
 *
 * **Как отчёт ушёл в группу — строкой над разделами:** ушёл, отправляется, не доставлен (с
 * причиной и сроком повтора) или не уходит вовсе. Не доставлен — отчёт всё равно здесь.
 *
 * **Неделя — в адресе раздела** (`?tab=report&week=…`): переживает перезагрузку и ссылку
 * коллеге; пусто — прошлая неделя. Смена недели — не перезагрузка: прежний отчёт стоит
 * приглушённым, пока едет новый.
 */

import {
  Alert,
  Box,
  Button,
  Group,
  List,
  Loader,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
} from '@mantine/core';
import { useMutation } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { saveFile } from '../api/donors';
import { downloadSalesWeekly } from '../api/salesWeekly';
import type { SalesWeeklyView, WeeklyNumbers, WeeklyRow, WeeklySection } from '../api/salesWeekly';
import { InfoHint } from '../components/InfoHint';
import { Metric } from '../components/Metric';
import { formatNumber } from '../format';
import { notify } from '../notices';
import { FixedTable, NameCell } from './FixedTable';
import type { Column } from './FixedTable';
import { deliveryWords, useSalesWeekly, weekWords } from './weeklyData';

/** Числа таблицы по гипотезам — заголовок колонки и число из ответа сервера. Списки (письма по
 *  шагам, ответы по видам, лиды) — в разделах словами. */
const NUMBERS: { title: string; value: (numbers: WeeklyNumbers) => number }[] = [
  { title: 'Загружено', value: (numbers) => numbers.loaded },
  { title: 'Отправлено', value: (numbers) => numbers.sent },
  { title: 'Доставлено', value: (numbers) => numbers.delivered },
  { title: 'Ответов', value: (numbers) => numbers.answers },
  { title: 'В диалоге', value: (numbers) => numbers.active + numbers.silent },
  { title: 'Передано', value: (numbers) => numbers.handed },
  { title: 'В очереди', value: (numbers) => numbers.letters_queued },
];

const COLUMNS: Column[] = [
  { title: 'Гипотеза', width: '14rem' },
  ...NUMBERS.map((number) => ({ title: number.title, width: '7.5rem' })),
];

/** Уже этого таблица уезжает в прокрутку: 14rem имени и семь колонок по 7.5rem. */
export const WEEKLY_MIN_WIDTH = 1064;

/** Вступление одной строкой, как считается — в «i». */
function Intro() {
  return (
    <Group gap={4} wrap="nowrap" align="flex-start" px="md">
      <Text size="sm" className="weeklyIntro">
        Недельный отчёт: контакты, отправки, доставляемость, ответы, диалоги, лиды поимённо,
        проблемы и план — за неделю с понедельника по воскресенье по Москве.
      </Text>
      <InfoHint name="Как считается отчёт" width={360}>
        Числа — те же, что на вкладках: воронка недели — по первому письму лида, как на «Воронке» за
        те же даты; ответы, жалобы, паузы и передачи — по своему времени в неделе; диалоги и план —
        на момент отчёта. MQL и SQL ставит телемаркетолог в Kommo — лид отчёта «передан
        телемаркетологу». Тот же текст уходит в группу продаж в Telegram.
      </InfoHint>
    </Group>
  );
}

function Toolbar({ view, onWeek }: { view: SalesWeeklyView; onWeek: (week: string) => void }) {
  const file = useMutation({
    mutationFn: () => downloadSalesWeekly(view.week),
    onSuccess: (found) => {
      saveFile(found, `sales-week-${view.week}.csv`);
      notify({ message: `Отчёт за ${weekWords(view.week)} выгружен` });
    },
    onError: (failure) =>
      notify({ title: 'Выгрузка не удалась', message: refusalOf(failure), color: 'red' }),
  });
  return (
    <Group align="flex-end" gap="md" wrap="wrap" px="md">
      <Select
        label="Неделя"
        allowDeselect={false}
        // Неделя из старой ссылки старше списка выбора — она стоит в нём первой, а не пустым полем.
        data={(view.weeks.includes(view.week) ? view.weeks : [view.week, ...view.weeks]).map(
          (week) => ({ value: week, label: weekWords(week) }),
        )}
        value={view.week}
        onChange={(value) => {
          if (value !== null) onWeek(value);
        }}
        w={{ base: '100%', xs: 240 }}
      />
      <Button variant="light" loading={file.isPending} onClick={() => file.mutate()}>
        Выгрузить CSV
      </Button>
    </Group>
  );
}

function Tiles({ total }: { total: WeeklyNumbers }) {
  return (
    <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }} spacing="sm" px="md" className="weeklyTiles">
      <Metric
        title="Загружено"
        value={formatNumber(total.loaded)}
        hint={`готово к письмам — ${formatNumber(total.ready)}`}
      />
      <Metric title="Первое письмо" value={formatNumber(total.sent)} hint="лидов, не писем" />
      <Metric
        title="Доставлено"
        value={formatNumber(total.delivered)}
        hint={`отказ — ${formatNumber(total.bounced)}`}
      />
      <Metric
        title="Ответов"
        value={formatNumber(total.answers)}
        hint={`автоответов — ${formatNumber(total.replies.auto_reply ?? 0)}`}
      />
      <Metric
        title="В диалоге"
        value={formatNumber(total.active + total.silent)}
        hint={`молчащих — ${formatNumber(total.silent)}`}
      />
      <Metric title="Передано" value={formatNumber(total.handed)} hint="телемаркетологу" />
    </SimpleGrid>
  );
}

/** Разделы — теми же словами, что в группе: первая строка — итог, дальше — список. */
function Sections({ sections }: { sections: WeeklySection[] }) {
  return (
    <Stack gap="sm" px="md" className="weeklySections">
      {sections.map((section, index) => {
        const [first, ...rest] = section.lines;
        return (
          <Box key={section.key} className="weeklySection">
            <Text size="sm" fw={600}>
              {index + 1}. {section.title}
            </Text>
            <Text size="sm" className="wrapCell">
              {first}
            </Text>
            {rest.length > 0 ? (
              <List size="sm" spacing={2} mt={4} className="wrapCell">
                {rest.map((line, at) => (
                  <List.Item key={at}>{line}</List.Item>
                ))}
              </List>
            ) : null}
          </Box>
        );
      })}
    </Stack>
  );
}

function ByHypothesis({ view, stale }: { view: SalesWeeklyView; stale: boolean }) {
  const line = (key: string, name: string, numbers: WeeklyNumbers, total = false) => (
    <Table.Tr key={key}>
      <NameCell name={name} total={total} />
      {NUMBERS.map((number) => (
        <Table.Td key={number.title}>{formatNumber(number.value(numbers))}</Table.Td>
      ))}
    </Table.Tr>
  );
  return (
    <Stack gap={6}>
      <Text size="sm" px="md" className="weeklyTableNote">
        По гипотезам: отправлено и доставлено — лиды первого письма недели; ответы и передачи — за
        неделю; диалоги и очередь — на момент отчёта.
      </Text>
      <FixedTable
        columns={COLUMNS}
        minWidth={WEEKLY_MIN_WIDTH}
        className="weeklyTable"
        horizontalSpacing="md"
        tabularNums
        stale={stale}
        label="Недельный отчёт по гипотезам"
        rows={[
          ...view.rows.map((row: WeeklyRow) =>
            line(String(row.hypothesis_id), row.name, row.numbers),
          ),
          line('total', 'Всего', view.total, true),
        ]}
      />
    </Stack>
  );
}

/** Как отчёт ушёл в группу: обычной строкой; не доставлен — плашкой, янтарь, пока идут повторы,
 *  роза — когда их больше нет (как «Модуль продаж выключен», `ModuleOff`). */
function Delivery({ view }: { view: SalesWeeklyView }) {
  const said = deliveryWords(view.delivery);
  if (said.color === undefined) {
    return (
      <Text size="sm" c="dimmed" px="md" className="weeklyDelivery">
        {said.text}
      </Text>
    );
  }
  return (
    <Alert color={said.color} py={6} px="sm" mx="md" className="weeklyDelivery">
      <Text size="sm">{said.text}</Text>
    </Alert>
  );
}

interface PaneProps {
  /** Понедельник недели из адреса раздела; пусто — прошлая неделя. */
  week: string;
  onWeek: (week: string) => void;
}

export function WeeklyPane({ week, onWeek }: PaneProps) {
  const report = useSalesWeekly(week);
  const view = report.data;
  if (view === undefined) {
    return report.error ? (
      <Alert color="red" title="Отчёт не загрузился" mx="md">
        {refusalOf(report.error)}
      </Alert>
    ) : (
      <Loader aria-label="Загружаем отчёт" m="md" />
    );
  }
  const stale = report.isPlaceholderData;
  return (
    <Stack gap="md">
      <Intro />
      <Toolbar view={view} onWeek={onWeek} />
      {report.error ? (
        <Alert color="red" title="Отчёт этой недели не загрузился" mx="md">
          {refusalOf(report.error)}
        </Alert>
      ) : null}
      <Stack
        gap="md"
        className="staleRows"
        data-stale={stale || undefined}
        aria-busy={stale || undefined}
      >
        <Delivery view={view} />
        <Tiles total={view.total} />
        <Sections sections={view.sections} />
        <ByHypothesis view={view} stale={stale} />
      </Stack>
    </Stack>
  );
}
