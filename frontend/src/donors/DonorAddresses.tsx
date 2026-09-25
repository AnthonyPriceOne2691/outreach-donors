/**
 * Адреса донора в его карточке: что есть, откуда, чем кончился поиск и —
 * если адреса нет — поиск для этого донора.
 *
 * До 25.09.2026 поиск адресов ставился только всем сразу, блоком над
 * таблицей доноров. Замечание: «спрятать контакты в карточку к каждому
 * донору». Отсюда этот раздел.
 *
 * **Кнопка — только без адреса.** Адрес есть — искать нечего; срок его
 * годности ведёт общий поиск.
 *
 * **Почему нельзя — до нажатия.** Поиск ставится только тем, кого общий
 * поиск взял бы сам: подходящим, принятым человеком, без свежей попытки.
 * Решает сервер и присылает с карточкой причину отказа словами — экран её
 * показывает, а не выясняет отказом после.
 *
 * **Задача переживает уход с экрана.** Её номер помнится в браузере
 * по донору: вернувшись в карточку, человек видит, чем кончился поиск,
 * а не кнопку, которая поставила бы второй.
 */

import { Badge, Button, Card, Group, Stack, Table, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';

import { refusalOf } from '../api/client';
import { searchDonorContact } from '../api/contacts';
import { fetchJob } from '../api/jobs';
import { CONTACT_SOURCES, CONTACT_STATUSES, NOT_SEARCHED, PERMISSION_TITLES } from '../api/labels';
import type { DonorFullCard, JobCard, JobState } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { formatDate } from '../format';
import { JobLine } from '../jobs/JobLine';
import { CONTACTS_QUERY_KEY } from './PendingContacts';

/** Пока задача не кончилась, второй поиск не предлагается. */
const ACTIVE: ReadonlySet<JobState> = new Set(['queued', 'running', 'retry_wait']);

function jobKey(donorId: number): string {
  return `donor:${donorId}:contacts-job`;
}

function remembered(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function remember(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // Хранилище недоступно (приватное окно) — помним до перезагрузки.
  }
}

function SearchState({ donor }: { donor: DonorFullCard }) {
  if (donor.contact_attempted_at === null) {
    return (
      <Text size="sm" c="dimmed">
        Адрес ещё не искали.
      </Text>
    );
  }
  return (
    <Text size="sm" c="dimmed">
      Искали {formatDate(donor.contact_attempted_at)}.
    </Text>
  );
}

function AddressTable({ donor }: { donor: DonorFullCard }) {
  return (
    // Таблица выступает на поле ячейки (`bleedTable`): адрес встаёт на ту же
    // левую линию, что заголовок раздела, а подсветка строки — шире текста.
    <Table.ScrollContainer minWidth={560} type="native" className="scrollSlim bleedTable">
      <Table className="dataTable donorAddresses" verticalSpacing="sm" horizontalSpacing="sm">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Адрес</Table.Th>
            <Table.Th>Откуда</Table.Th>
            <Table.Th>Писали</Table.Th>
            <Table.Th>Отвечали</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {donor.contacts.map((contact) => (
            <Table.Tr key={contact.id}>
              {/* Текст — в своём элементе: ячейка во всю ширину колонки, и замер
                  по ней делил бы фон с его же переливом, а не буквы и фон. */}
              <Table.Td>
                <span className="donorEmail">{contact.email}</span>
              </Table.Td>
              <Table.Td>
                <span className="donorSource">
                  {CONTACT_SOURCES[contact.source] ?? `другое (${contact.source})`}
                </span>
              </Table.Td>
              <Table.Td>{formatDate(contact.last_contacted_at)}</Table.Td>
              <Table.Td>
                {/* Отвечающий адрес важнее найденного: дальше пишем тому,
                    кто отвечает, а не в ящик, где письмо пролежало неделю. */}
                {contact.last_replied_at === null ? (
                  '—'
                ) : (
                  <Group justify="center">
                    <Badge variant="light" color="green">
                      {formatDate(contact.last_replied_at)}
                    </Badge>
                  </Group>
                )}
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

export function DonorAddresses({ donor }: { donor: DonorFullCard }) {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(() => remembered(jobKey(donor.id)));
  // Исход задачи читается из того же кэша, что у `JobLine`: второго опроса нет.
  const job = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => fetchJob(jobId ?? ''),
    enabled: jobId !== null,
    retry: false,
  });
  const searching = job.data !== undefined && ACTIVE.has(job.data.state);

  const start = useMutation({
    mutationFn: () => searchDonorContact(donor.id),
    onSuccess: (queued) => {
      remember(jobKey(donor.id), queued.job_id);
      setJobId(queued.job_id);
      notifications.show({ message: 'Поиск адреса поставлен в очередь', color: 'green' });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не поставили', message: refusalOf(failure), color: 'red' }),
  });

  // Кончился поиск — перечитать карточку (адрес, исход, причина отказа),
  // таблицу и число ждущих на экране списка. Удачный исход в следующий раз
  // не показывается: его и так рассказывают значок и дата поиска выше.
  // Упавший — показывается, пока не поставят новый: причина нужна и завтра.
  const finished = useCallback(() => {
    if (queryClient.getQueryData<JobCard>(['job', jobId])?.state === 'done') {
      remember(jobKey(donor.id), null);
    }
    void queryClient.invalidateQueries({ queryKey: ['donor', String(donor.id)] });
    void queryClient.invalidateQueries({ queryKey: ['donors'] });
    void queryClient.invalidateQueries({ queryKey: CONTACTS_QUERY_KEY });
  }, [queryClient, donor.id, jobId]);

  const state =
    donor.contact_status === null ? NOT_SEARCHED : CONTACT_STATUSES[donor.contact_status];
  const empty = donor.contacts.length === 0;
  const mayStart = empty && donor.contact_refusal === null && !searching;

  return (
    <Card className="glass" p="xl">
      <Stack gap="sm">
        <Group justify="space-between" align="center" gap="sm">
          <Title order={5}>Адреса</Title>
          <Badge variant="light" color={state.color}>
            {state.title}
          </Badge>
        </Group>
        <SearchState donor={donor} />

        {!empty && <AddressTable donor={donor} />}

        {empty && donor.contact_refusal !== null && <Text size="sm">{donor.contact_refusal}</Text>}
        {mayStart && (
          <Stack gap="xs" align="flex-start">
            <Text size="sm">
              Лестница идёт от бесплатных ступеней к платной: почтовая запись, страницы сайта,
              данные регистратора и только потом платный сервис.
              {can('run') ? '' : ` Поиск ставит сотрудник с правом «${PERMISSION_TITLES.run}».`}
            </Text>
            {can('run') && (
              <Button className="press" loading={start.isPending} onClick={() => start.mutate()}>
                Найти адрес
              </Button>
            )}
          </Stack>
        )}
        {jobId !== null && <JobLine jobId={jobId} onFinished={finished} />}
      </Stack>
    </Card>
  );
}
