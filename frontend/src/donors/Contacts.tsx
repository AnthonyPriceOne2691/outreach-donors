/**
 * Поиск контактов с экрана.
 *
 * До этой карточки лестницу контактов запускала только консольная
 * команда: прогон находил доноров, а чтобы найти им адреса, нужен был
 * инженер — при том что приёмка требует ровно обратного.
 *
 * **Идёт задачей, а не запросом.** Сотня доменов — это минуты; ответ
 * в том же запросе означал бы потерянную работу, если человек закрыл
 * вкладку. Пока задача идёт, карточка спрашивает состояние сама.
 *
 * **Отчёт прошлого прохода показан по ступеням.** Отношение «вошло»
 * к «нашли» на каждой ступени — это и есть проверка порядка ступеней,
 * ради которого лестница так устроена.
 */

import { Alert, Button, Card, Group, Loader, Stack, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { fetchContactsState, searchContacts } from '../api/contacts';
import { useSession } from '../auth/AuthProvider';
import { JobOutcome } from '../jobs/JobLine';

const CONTACTS_QUERY_KEY = ['contacts-state'] as const;

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

function line(last: Record<string, unknown> | null): string | null {
  if (last === null) return null;
  const counters = (last.counters ?? {}) as Record<string, number>;
  const saved = Number(last.saved ?? 0);
  const walked = Number(last.walked ?? 0);
  const paid = Number(counters.provider_entered ?? 0);
  const forms = Number(counters.form_only ?? 0);
  return `Прошлый проход: доменов ${walked}, адресов ${saved}, платных запросов ${paid}, форм ${forms}.`;
}

export function Contacts() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: CONTACTS_QUERY_KEY,
    queryFn: fetchContactsState,
    // Пока задача идёт, спрашиваем чаще: человек смотрит на экран
    // именно в эти минуты.
    refetchInterval: (query) => (query.state.data?.running ? 5_000 : false),
  });

  const start = useMutation({
    mutationFn: () => searchContacts({ limit: 100 }),
    onSuccess: async (queued) => {
      await queryClient.invalidateQueries({ queryKey: CONTACTS_QUERY_KEY });
      notifications.show({
        message: `Поиск поставлен в очередь: ждёт ${queued.pending} доноров`,
        color: 'green',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не поставили', message: refusalOf(failure), color: 'red' }),
  });

  if (isLoading) return <Loader aria-label="Смотрим, кому нужен контакт" size="sm" m="md" />;

  const pending = data?.pending ?? 0;
  const running = data?.running ?? false;
  const report = line(data?.last ?? null);

  return (
    <Card className="glassPanel" p="xl">
      <Stack gap="sm">
        <Group justify="space-between" align="flex-start">
          <Stack gap={6}>
            <Title order={4}>Контакты</Title>
            <Text size="sm" c="dimmed" maw={620}>
              Лестница идёт от бесплатных ступеней к платной: почтовая запись, страницы сайта,
              данные регистратора и только потом платный сервис — и только тем, кому не нашлось
              иначе.
            </Text>
          </Stack>
          {can('run') && (
            <Button
              className="press"
              loading={start.isPending}
              disabled={pending === 0 || running}
              onClick={() => start.mutate()}
            >
              {running ? 'Идёт поиск…' : 'Найти контакты'}
            </Button>
          )}
        </Group>

        <Text size="sm">
          Ждут контакта: <b>{pending}</b>
          {pending === 0 ? ' — все пройдены или ещё не отобраны' : ''}
        </Text>

        {running && data?.workers === 0 && (
          <Alert color="yellow" title="Задачу некому взять">
            Поиск поставлен, но воркеров на очереди нет — он не начнётся, пока их не поднимут.
          </Alert>
        )}

        {report !== null && !running && (
          <Text size="sm" c="dimmed">
            {report}
          </Text>
        )}
        {data?.job && data.job.state !== 'done' && data.job.state !== 'running' && (
          <JobOutcome job={data.job} />
        )}
      </Stack>
    </Card>
  );
}
