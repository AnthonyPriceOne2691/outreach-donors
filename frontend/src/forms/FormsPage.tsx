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

import { fetchForms, formFilled, formGaveUp } from '../api/contacts';
import type { FormCard } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { formatCompact, formatDate } from '../format';

const FORMS_QUERY_KEY = ['forms'] as const;

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

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

      <Card className="glassPanel" p="xl">
        {rows.length === 0 ? (
          <Text size="sm" c="dimmed">
            Очередь пуста. Сюда попадают доноры, у которых лестница нашла форму, но не нашла адреса.
          </Text>
        ) : (
          <Table className="dataTable" verticalSpacing="sm" horizontalSpacing="md" miw={760}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Донор</Table.Th>
                <Table.Th>DR</Table.Th>
                <Table.Th>Трафик</Table.Th>
                <Table.Th>Искали</Table.Th>
                {mayWork ? <Table.Th /> : null}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((row) => (
                <Table.Tr key={row.donor_id}>
                  <Table.Td>
                    <Anchor href={`https://${row.host}`} target="_blank" rel="noreferrer">
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
                      <Group gap="xs" justify="flex-end">
                        <Button
                          size="compact-sm"
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
                          loading={giveUp.isPending && giveUp.variables?.donor_id === row.donor_id}
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
