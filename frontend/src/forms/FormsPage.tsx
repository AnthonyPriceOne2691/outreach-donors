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
 * пяти, и значок DR «100» ужимался до «1…» (аудит 25.09.2026). Кнопки —
 * по центру своей колонки, как всё, кроме имени, а не прижаты вправо.
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
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { fetchForms, formFilled, formGaveUp } from '../api/contacts';
import type { FormCard } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { formatCompact, formatDate } from '../format';

const FORMS_QUERY_KEY = ['forms'] as const;

/** Колонки слева направо. Ширина первой — остаток: в ней домен. Остальные —
 *  по самому длинному содержимому, замеренному шрифтом экрана 25.09.2026:
 *  значок «100» — 44 px, «12,3 млрд», дата; кнопки «Вписать адрес» и «Не
 *  вышло» с зазором — 221 (на глаз вышло 196, и кнопки обрезались до
 *  «Вписать адре»). Плюс 32 px полей ячейки. */
const COLUMNS: { title: string; width?: string }[] = [
  { title: 'Донор' },
  { title: 'DR', width: '5rem' },
  { title: 'Трафик', width: '7.5rem' },
  { title: 'Искали', width: '8rem' },
];
const ACTIONS_WIDTH = '16.25rem';

/** Уже этого таблица уезжает в прокрутку: колонкам — их ширины (588 px),
 *  домену — не меньше двухсот. */
const TABLE_MIN_WIDTH = 790;

export function FormsPage() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [filling, setFilling] = useState<FormCard | null>(null);
  const [email, setEmail] = useState('');

  const { data, isLoading, error } = useQuery({ queryKey: FORMS_QUERY_KEY, queryFn: fetchForms });

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
  const mayWork = can('run');

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
      <Card className="glassPanel" p={rows.length === 0 ? 'xl' : 'md'}>
        {rows.length === 0 ? (
          <Text size="sm" c="dimmed">
            Очередь пуста. Сюда попадают доноры, у которых лестница нашла форму, но не нашла адреса.
          </Text>
        ) : (
          <Table.ScrollContainer minWidth={TABLE_MIN_WIDTH} type="native" className="scrollSlim">
            <Table
              className="dataTable fixedTable"
              layout="fixed"
              tabularNums
              verticalSpacing="sm"
              horizontalSpacing="md"
            >
              <colgroup>
                {COLUMNS.map((column) => (
                  <col
                    key={column.title}
                    style={column.width ? { width: column.width } : undefined}
                  />
                ))}
                {mayWork ? <col style={{ width: ACTIONS_WIDTH }} /> : null}
              </colgroup>
              <Table.Thead>
                <Table.Tr>
                  {COLUMNS.map((column) => (
                    <Table.Th key={column.title}>{column.title}</Table.Th>
                  ))}
                  {mayWork ? <Table.Th /> : null}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
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
