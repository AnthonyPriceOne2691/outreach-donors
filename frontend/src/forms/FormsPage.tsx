/**
 * Ручная очередь: доноры, у которых форма вместо адреса.
 *
 * Ступень лестницы, которую нельзя пройти кодом. До этого экрана
 * очередь существовала только значком «только форма» в общей таблице:
 * увидеть её списком и работать с ней было нельзя.
 *
 * **Потолок в месяц показан числом, а не спрятан в настройках.**
 * Человек, разбирающий пачку, должен видеть, сколько ещё можно взять:
 * формы на тысяче доменов — это десятки, на пятидесяти тысячах —
 * тысячи, и очередь без потолка никто никогда не разберёт.
 *
 * **Два исхода, и оба закрывают строку.** Донор дал адрес — он
 * становится обычным и уйдёт в очередь писем. Не вышло — закрываем
 * без адреса: висеть здесь вечно строка не должна, а срок годности
 * вернёт донора, если что-то изменится.
 *
 * **Таблица — с шириной колонок по самому длинному и прокруткой на узком
 * окне.** В карточке без прокрутки на телефоне были видны две колонки из
 * пяти, и значок DR «100» ужимался до «1…» (аудит 25.09.2026).
 *
 * **Все колонки по центру, и донор тоже; у кнопок — заголовок «Действия»;
 * по двадцать на странице** (замечание 28.09.2026: «центрировать таблицу,
 * добавить в последнюю колонку шапку „Действия“, первую колонку сделать уже,
 * пагинация, 20 записей на странице»). Донору до этого доставался весь
 * остаток ширины — 698 px из 1 286 на широком экране, — и колонки справа
 * жались к краю. Теперь у колонок доли (`COLUMNS`), страница — на сервере
 * и в адресе (`?page=2`), размер страницы называет сервер.
 */

import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Modal,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { refusalOf } from '../api/client';
import { fetchForms, formFilled, formGaveUp } from '../api/contacts';
import type { FormCard } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { PageSwitch, usePageParam } from '../components/PageSwitch';
import { formatCompact, formatDate } from '../format';

const FORMS_QUERY_KEY = ['forms'] as const;

/** Колонки слева направо и их доли. Доли — чтобы расстояние между
 *  содержимым соседних колонок было одним и тем же: у каждой колонки
 *  ширина — её обычное содержимое плюс общий запас (донор ~130 px, значок
 *  DR — 44, «12,3 млрд», дата, две кнопки — 221 px). Уже `TABLE_MIN_WIDTH`
 *  таблица не сжимается: на нём каждая колонка вмещает самое длинное, что
 *  в ней бывает, — кнопки «Вписать адрес» и «Не вышло» с зазором и полями
 *  ячейки (260 px; на глаз вышло 196, и кнопки обрезались до «Вписать
 *  адре»), дата и число — по своим 128 и 120. Длинный домен переносится. */
const COLUMNS: { title: string; share: number }[] = [
  { title: 'Донор', share: 22 },
  { title: 'DR', share: 15 },
  { title: 'Трафик', share: 17 },
  { title: 'Искали', share: 17 },
];
const ACTIONS = { title: 'Действия', share: 29 };

/** Уже этого таблица уезжает в прокрутку: с кнопками — 29% не меньше их
 *  260 px, без кнопок — донору его двести. */
const TABLE_MIN_WIDTH = { withActions: 900, readOnly: 650 } as const;

export function FormsPage() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [filling, setFilling] = useState<FormCard | null>(null);
  const [email, setEmail] = useState('');

  const [page, goToPage] = usePageParam();

  const { data, isLoading, error, isPlaceholderData } = useQuery({
    queryKey: [...FORMS_QUERY_KEY, page],
    queryFn: () => fetchForms(page),
    // Пока едет следующая страница, стоит прежняя: пустая таблица на долю
    // секунды читалась бы как «очередь пуста».
    placeholderData: keepPreviousData,
  });
  const pages = data === undefined ? 1 : Math.max(1, Math.ceil(data.total / data.limit));
  const settled = data !== undefined && !isPlaceholderData;

  // Страница за концом — ссылка, открытая после разбора очереди, или
  // последний донор последней страницы, закрытый только что. Сервер отдаёт
  // её пустой и говорит, сколько всего; экран уходит на последнюю, заменяя
  // адрес, а не добавляя.
  useEffect(() => {
    if (settled && page > pages) goToPage(pages, true);
  }, [settled, page, pages, goToPage]);

  const done = async (message: string) => {
    await queryClient.invalidateQueries({ queryKey: FORMS_QUERY_KEY });
    setFilling(null);
    setEmail('');
    notifications.show({ message, color: 'green' });
  };

  const fill = useMutation({
    mutationFn: (row: FormCard) => formFilled(row.donor_id, email.trim()),
    onSuccess: (row) => done(`${row.host}: адрес записан, донор пойдёт в очередь писем`),
    onError: (failure) =>
      notifications.show({ title: 'Не записали', message: refusalOf(failure), color: 'red' }),
  });

  const giveUp = useMutation({
    mutationFn: (row: FormCard) => formGaveUp(row.donor_id, null),
    onSuccess: (row) => done(`${row.host} закрыт без адреса`),
    onError: (failure) =>
      notifications.show({ title: 'Не закрыли', message: refusalOf(failure), color: 'red' }),
  });

  if (isLoading) return <Loader aria-label="Загружаем очередь форм" m="md" />;
  if (error) {
    return (
      <Alert color="red" title="Очередь не загрузилась" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }

  const rows = data?.rows ?? [];
  // Пуста очередь, а не страница: страница за концом — переход на последнюю,
  // а не «очередь пуста» на мгновение перед ним.
  const empty = (data?.total ?? 0) === 0;
  const mayWork = can('run');
  const columns = mayWork ? [...COLUMNS, ACTIONS] : COLUMNS;
  const shares = columns.reduce((sum, column) => sum + column.share, 0);

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="sm">
          <Title order={3}>Формы</Title>
          <Text size="sm" c="dimmed" maw={680}>
            У этих доноров есть контактная форма и нет почты. Лестница дошла до них и остановилась:
            дальше нужны руки. Заполнили и получили адрес — впишите его, и донор пойдёт в очередь
            писем обычным порядком.
          </Text>
          <Text size="sm">
            В очереди <b>{data?.total ?? 0}</b>. Потолок на месяц: <b>{data?.monthly_left ?? 0}</b>{' '}
            из {data?.monthly_cap ?? 0} осталось.
          </Text>
          {(data?.monthly_left ?? 0) === 0 && (
            <Alert color="yellow" title="Месячный потолок выбран">
              Новые формы в очередь не попадут до следующего месяца. Потолок нужен, чтобы очередь
              оставалась той, которую реально разбирают.
            </Alert>
          )}
        </Stack>
      </Card>

      {/* Поля карточки с таблицей — вместе с полем ячейки те же 32 px, что у
          панели сверху: иначе текст соседних карточек начинается с разных
          мест (аудит 25.09.2026). */}
      <Card className="glassPanel" p={empty ? 'xl' : 'md'}>
        {empty ? (
          <Text size="sm" c="dimmed">
            Очередь пуста. Сюда попадают доноры, у которых лестница нашла форму, но не нашла адреса.
          </Text>
        ) : (
          <Stack gap="sm">
            <Table.ScrollContainer
              minWidth={mayWork ? TABLE_MIN_WIDTH.withActions : TABLE_MIN_WIDTH.readOnly}
              type="native"
              className="scrollSlim"
            >
              <Table
                className="dataTable fixedTable allCentered"
                layout="fixed"
                tabularNums
                verticalSpacing="sm"
                horizontalSpacing="md"
              >
                <colgroup>
                  {columns.map((column) => (
                    <col
                      key={column.title}
                      style={{ width: `${(column.share / shares) * 100}%` }}
                    />
                  ))}
                </colgroup>
                <Table.Thead>
                  <Table.Tr>
                    {columns.map((column) => (
                      <Table.Th key={column.title}>{column.title}</Table.Th>
                    ))}
                  </Table.Tr>
                </Table.Thead>
                {/* Строки прежней страницы, пока едет новая, приглушены, и
                    кнопки в них заперты: решать по ним нельзя — они не той
                    страницы. */}
                <Table.Tbody
                  className="staleRows"
                  data-stale={isPlaceholderData || undefined}
                  aria-busy={isPlaceholderData || undefined}
                >
                  {rows.map((row) => (
                    <Table.Tr key={row.donor_id}>
                      <Table.Td>
                        {/* Имя — чернилами, как у доноров и диалогов: бирюзовая
                            ссылка в верхних строках стоит на бирюзовом углу
                            полотна, и замер дал 4,30 : 1 при норме 4,5
                            (25.09.2026). Что это ссылка, говорит подчёркивание
                            под курсором. */}
                        <Anchor
                          href={`https://${row.host}`}
                          target="_blank"
                          rel="noreferrer"
                          c="var(--ink)"
                          fw={500}
                          underline="hover"
                          className="cellName"
                        >
                          {row.host}
                        </Anchor>
                      </Table.Td>
                      <Table.Td>
                        <Badge variant="light">{row.dr ?? '—'}</Badge>
                      </Table.Td>
                      <Table.Td>{formatCompact(row.org_traffic)}</Table.Td>
                      <Table.Td>{formatDate(row.attempted_at)}</Table.Td>
                      {mayWork ? (
                        <Table.Td>
                          <Group gap="xs" justify="center" wrap="nowrap">
                            <Button
                              size="compact-sm"
                              className="press"
                              disabled={isPlaceholderData}
                              onClick={() => {
                                setFilling(row);
                                setEmail('');
                              }}
                            >
                              Вписать адрес
                            </Button>
                            <Button
                              size="compact-sm"
                              variant="subtle"
                              color="gray"
                              className="press"
                              disabled={isPlaceholderData}
                              loading={
                                giveUp.isPending && giveUp.variables?.donor_id === row.donor_id
                              }
                              onClick={() => giveUp.mutate(row)}
                            >
                              Не вышло
                            </Button>
                          </Group>
                        </Table.Td>
                      ) : null}
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
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

      <Modal
        opened={filling !== null}
        onClose={() => setFilling(null)}
        title={`Адрес донора ${filling?.host ?? ''}`}
      >
        <Stack gap="sm">
          <Text size="sm">
            Адрес, который донор дал в ответ на форму. Он попадёт в базу как введённый руками — в
            журнале останется, кто его внёс.
          </Text>
          <TextInput
            label="Адрес почты"
            placeholder="editor@site.com"
            value={email}
            onChange={(event) => setEmail(event.currentTarget.value)}
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setFilling(null)}>
              Отмена
            </Button>
            <Button
              loading={fill.isPending}
              disabled={!email.includes('@')}
              onClick={() => filling && fill.mutate(filling)}
            >
              Записать
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
