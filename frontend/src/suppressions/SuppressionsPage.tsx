/**
 * Стоп-лист: кому мы не пишем и почему.
 *
 * Экран отвечает на два вопроса, которые до него задавали инженеру:
 * «почему этому донору не ушло письмо» и «как вернуть того, кого
 * отписали по ошибке».
 *
 * **Решение адресата и наше собственное выглядят по-разному.** Отписка
 * и жалоба — просьба человека, и снять их можно только объяснив, зачем;
 * поставщик и ручная запись — наше решение, и отменяется оно молча.
 * Разницу считает сервер (`donor_decision`): свой экземпляр правила
 * здесь разошёлся бы с ним на первой новой причине.
 *
 * **Возврат не воскрешает письма.** Снятая запись открывает дорогу
 * будущим письмам, а снятые с очереди остаются снятыми — об этом
 * сказано на самом экране, а не в документации: человек, возвращающий
 * донора, документацию в этот момент не читает.
 *
 * **Срок — только у наших записей.** Требование исключает тех, у кого
 * агентство размещалось «за последние 12 месяцев», то есть окном,
 * а не навсегда. Поэтому в форме два варианта, и «навсегда» стоит
 * первым: срок — исключение, а не правило. Отписке и жалобе срок
 * не ставится вовсе, их руками здесь не заводят.
 *
 * **Истёкшая запись остаётся на экране.** Удалив её, мы потеряли бы
 * ответ на вопрос «почему ему полгода не писали» — а его сюда и приходят
 * задавать. Она помечена и вынесена числом в шапку: список из одних
 * истёкших записей не то же самое, что пустой.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Modal,
  Select,
  Stack,
  Table,
  Text,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { addSuppression, listSuppressions, removeSuppression } from '../api/outreach';
import { SUPPRESSION_REASON_TITLES } from '../api/labels';
import type { StopEntry, SuppressionReason } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { formatDate } from '../format';

const STOP_LIST_QUERY_KEY = ['suppressions'] as const;

const HAND_REASONS: { value: SuppressionReason; label: string }[] = [
  { value: 'manual', label: 'вручную' },
  { value: 'supplier', label: 'поставщик' },
];

/** Сроки записи. «Навсегда» первым: оно и есть умолчание. */
const TERMS = [
  { value: 'forever', label: 'навсегда' },
  { value: 'year', label: '12 месяцев' },
] as const;

type Term = (typeof TERMS)[number]['value'];

/** Когда запись перестаёт держать. Год считается от сегодня — так
 *  требование и называет поставщиков: «размещались за последние 12 мес.». */
function endOf(term: Term): string | null {
  if (term === 'forever') return null;
  const until = new Date();
  until.setFullYear(until.getFullYear() + 1);
  return until.toISOString();
}

const when = formatDate;

export function SuppressionsPage() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [target, setTarget] = useState('');
  const [reason, setReason] = useState<SuppressionReason>('manual');
  const [term, setTerm] = useState<Term>('forever');
  const [removing, setRemoving] = useState<StopEntry | null>(null);
  const [why, setWhy] = useState('');

  const { data, isLoading, error } = useQuery({
    queryKey: STOP_LIST_QUERY_KEY,
    queryFn: listSuppressions,
  });

  const add = useMutation({
    mutationFn: () => addSuppression({ target: target.trim(), reason, expires_at: endOf(term) }),
    onSuccess: async (row) => {
      await queryClient.invalidateQueries({ queryKey: STOP_LIST_QUERY_KEY });
      setTarget('');
      notifications.show({
        message: `${row.host ?? row.email} в стоп-листе — письма сняты с очереди`,
        color: 'green',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не завели', message: refusalOf(failure), color: 'red' }),
  });

  const take = useMutation({
    mutationFn: (row: StopEntry) => removeSuppression(row.id, why.trim() || null),
    onSuccess: async (row) => {
      await queryClient.invalidateQueries({ queryKey: STOP_LIST_QUERY_KEY });
      setRemoving(null);
      setWhy('');
      notifications.show({
        message: `${row.host ?? row.email} снят со стоп-листа — новые письма ему снова уходят`,
        color: 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не сняли', message: refusalOf(failure), color: 'red' }),
  });

  if (isLoading) return <Loader aria-label="Загружаем стоп-лист" m="md" />;
  if (error) {
    return (
      <Alert color="red" title="Стоп-лист не загрузился" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }

  const rows = data?.rows ?? [];
  const mayChange = can('send');
  const needsWhy = removing?.donor_decision === true;

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="sm">
          <Title order={3}>Стоп-лист</Title>
          <Text size="sm" c="dimmed" maw={680}>
            Кому мы не пишем ни на одном этапе. Проверяется дважды: при отборе доменов — домен из
            списка в прогон не идёт и юнитов на него не тратится, — и перед каждой отправкой, так
            что письмо адресату из списка не уйдёт, даже если его собрали раньше.
          </Text>
          <Text size="sm">
            Всего записей <b>{data?.total ?? 0}</b>, из них по решению адресата{' '}
            <b>{data?.donor_decisions ?? 0}</b>
            {data?.expired ? (
              <>
                , истекли и больше не держат <b>{data.expired}</b>
              </>
            ) : null}
            .
          </Text>
        </Stack>
      </Card>

      {mayChange ? (
        <Card className="glassPanel" p="xl">
          <Stack gap="sm">
            <Title order={4}>Завести запись</Title>
            <Text size="sm" c="dimmed" maw={680}>
              Домен закрывает сайт целиком, адрес — один ящик. Домен, которого ещё нет в базе,
              заводится вместе с записью: список поставщиков приходит раньше первого прогона.
            </Text>
            <Group align="flex-end" gap="sm">
              <TextInput
                label="Домен или адрес"
                placeholder="site.com или editor@site.com"
                value={target}
                onChange={(event) => setTarget(event.currentTarget.value)}
                w={320}
              />
              <Select
                label="Причина"
                data={HAND_REASONS}
                value={reason}
                onChange={(picked) => setReason((picked ?? 'manual') as SuppressionReason)}
                allowDeselect={false}
                w={200}
              />
              <Select
                label="Держит"
                data={TERMS.map((item) => ({ value: item.value, label: item.label }))}
                value={term}
                onChange={(picked) => setTerm((picked ?? 'forever') as Term)}
                allowDeselect={false}
                w={170}
              />
              <Button
                onClick={() => add.mutate()}
                loading={add.isPending}
                disabled={target.trim().length < 3}
              >
                Больше не писать
              </Button>
            </Group>
          </Stack>
        </Card>
      ) : null}

      <Card className="glassPanel" p="xl">
        {rows.length === 0 ? (
          <Text size="sm" c="dimmed">
            Список пуст. Сюда попадают те, кто отписался или пожаловался, и те, кого внесли руками.
          </Text>
        ) : (
          <Table className="dataTable" verticalSpacing="sm" horizontalSpacing="md" miw={760}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Кому не пишем</Table.Th>
                <Table.Th>Причина</Table.Th>
                <Table.Th>Кто завёл</Table.Th>
                <Table.Th>Когда</Table.Th>
                <Table.Th>Срок</Table.Th>
                {mayChange ? <Table.Th /> : null}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((row) => (
                <Table.Tr key={row.id}>
                  <Table.Td>{row.host ?? row.email}</Table.Td>
                  <Table.Td>
                    <Badge color={row.donor_decision ? 'red' : 'gray'} variant="light">
                      {SUPPRESSION_REASON_TITLES[row.reason]}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{row.created_by ?? '—'}</Table.Td>
                  <Table.Td>{when(row.created_at)}</Table.Td>
                  <Table.Td>
                    {row.expires_at === null ? (
                      <Text size="sm">навсегда</Text>
                    ) : row.expired ? (
                      <Badge color="gray" variant="outline">
                        истёк {when(row.expires_at)}
                      </Badge>
                    ) : (
                      <Text size="sm">до {when(row.expires_at)}</Text>
                    )}
                  </Table.Td>
                  {mayChange ? (
                    <Table.Td>
                      <Button
                        variant="subtle"
                        size="compact-sm"
                        onClick={() => {
                          setRemoving(row);
                          setWhy('');
                        }}
                      >
                        Снять
                      </Button>
                    </Table.Td>
                  ) : null}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>

      <Modal
        opened={removing !== null}
        onClose={() => setRemoving(null)}
        title={`Снять со стоп-листа ${removing?.host ?? removing?.email ?? ''}`}
      >
        <Stack gap="sm">
          {needsWhy ? (
            <Alert color="red" title="Это решение адресата">
              Он просил больше не писать. Снять запись можно, но причина уйдёт в журнал вместе с
              вашим именем.
            </Alert>
          ) : (
            <Text size="sm">
              Запись завели мы сами, объяснение не нужно. Письма, снятые с очереди, обратно не
              вернутся — очередь собирают заново.
            </Text>
          )}
          {needsWhy ? (
            <Textarea
              label="Почему снимаем"
              placeholder="Например: написал «пишите, передумал»"
              value={why}
              onChange={(event) => setWhy(event.currentTarget.value)}
              autosize
              minRows={2}
            />
          ) : null}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setRemoving(null)}>
              Оставить
            </Button>
            <Button
              color="red"
              loading={take.isPending}
              disabled={needsWhy && why.trim().length === 0}
              onClick={() => removing && take.mutate(removing)}
            >
              Снять запись
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
