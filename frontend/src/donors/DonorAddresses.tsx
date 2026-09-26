/**
 * Адреса донора в его карточке: что есть, откуда, на какой уйдёт письмо,
 * чем кончился поиск — и поиск адреса или адрес, вписанный руками.
 *
 * До 25.09.2026 поиск адресов ставился только всем сразу, блоком над
 * таблицей доноров. Замечание: «спрятать контакты в карточку к каждому
 * донору». Отсюда этот раздел.
 *
 * **Адрес руками — сколько угодно, по одному** (замечание 26.09.2026:
 * «проверь, чтобы в карточке донора была возможность заполнить контакты,
 * и не один ящик, а несколько»). Правило — то же, что у очереди форм
 * (`contacts/manual.py`): отказ словами сервера под полем, дубликат —
 * словами, в журнале — кто вписал. Удаляется только адрес, которому ещё
 * не писали; почему другой не удалить — сказано до нажатия.
 *
 * **На какой адрес уйдёт письмо — отметка сервера**, тем же запросом, что
 * у сборки писем (`Recipients.letter_address`), а не догадкой экрана: у
 * сборки есть стоп-лист и перепроверка адреса, и своя копия правила здесь
 * разошлась бы с ней. Письмо не соберётся ни на один — сказано почему.
 *
 * **Кнопка поиска — только без адреса.** Адрес есть — искать нечего; срок
 * его годности ведёт общий поиск. Почему нельзя — до нажатия: решает сервер
 * тем же правилом, что у общего поиска, и присылает причину с карточкой.
 *
 * **Задача переживает уход с экрана.** Её номер помнится в браузере
 * по донору: вернувшись в карточку, человек видит, чем кончился поиск,
 * а не кнопку, которая поставила бы второй.
 */

import {
  ActionIcon,
  Badge,
  Button,
  Card,
  Group,
  Popover,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconLock, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useState } from 'react';

import { refusalOf } from '../api/client';
import { addDonorAddress, removeDonorAddress, searchDonorContact } from '../api/contacts';
import { fetchJob } from '../api/jobs';
import { CONTACT_SOURCES, CONTACT_STATUSES, NOT_SEARCHED, PERMISSION_TITLES } from '../api/labels';
import type { ContactCard, DonorFullCard, JobCard, JobState } from '../api/types';
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

/** Фраза сервера — предложением: с заглавной и с точкой. */
function sentence(text: string): string {
  const said = text.charAt(0).toUpperCase() + text.slice(1);
  return said.endsWith('.') ? said : `${said}.`;
}

function SearchState({ donor }: { donor: DonorFullCard }) {
  if (donor.contact_attempted_at !== null) {
    return (
      <Text size="sm" c="dimmed">
        Искали {formatDate(donor.contact_attempted_at)}.
      </Text>
    );
  }
  // Вписанный руками адрес — «адрес найден», но поиска не было: дата
  // поиска здесь была бы выдумкой.
  const written = donor.contacts.some((contact) => contact.source === 'manual');
  return (
    <Text size="sm" c="dimmed">
      {written ? 'Адрес вписан вручную, лестницей не искали.' : 'Адрес ещё не искали.'}
    </Text>
  );
}

/** Почему адрес не удалить — за значком, целиком по нажатию: текст длинный,
 *  а наведения на телефоне нет. */
function KeptReason({ contact }: { contact: ContactCard }) {
  return (
    <Popover
      position="bottom-end"
      withArrow
      width={300}
      radius="lg"
      shadow="md"
      trapFocus
      returnFocus
      middlewares={{ shift: { padding: 16 } }}
    >
      <Popover.Target>
        <ActionIcon
          variant="subtle"
          color="gray"
          radius="xl"
          aria-label={`Почему не удалить ${contact.email}`}
        >
          <IconLock size={16} />
        </ActionIcon>
      </Popover.Target>
      <Popover.Dropdown className="glassSolid">
        <Text size="sm">{contact.removal_refusal}</Text>
      </Popover.Dropdown>
    </Popover>
  );
}

interface RemoveProps {
  contact: ContactCard;
  pending: boolean;
  onRemove: (contact: ContactCard) => void;
}

/** Удалить — вторым нажатием: найденный платной ступенью адрес иначе
 *  терялся бы одним промахом, а вернуть его можно только новым поиском. */
function RemoveAddress({ contact, pending, onRemove }: RemoveProps) {
  const [opened, setOpened] = useState(false);
  return (
    <Popover
      opened={opened}
      onChange={setOpened}
      position="bottom-end"
      withArrow
      width={300}
      radius="lg"
      shadow="md"
      trapFocus
      returnFocus
      middlewares={{ shift: { padding: 16 } }}
    >
      <Popover.Target>
        <ActionIcon
          variant="subtle"
          color="gray"
          radius="xl"
          aria-label={`Удалить ${contact.email}`}
          onClick={() => setOpened((was) => !was)}
        >
          <IconTrash size={16} />
        </ActionIcon>
      </Popover.Target>
      <Popover.Dropdown className="glassSolid">
        <Stack gap="sm">
          <Text size="sm">
            Удалить {contact.email}? Вернуть адрес можно, только вписав его заново
            {contact.source === 'manual' ? '' : ' или новым поиском'}.
          </Text>
          <Group gap="xs" justify="flex-end">
            <Button variant="default" size="compact-sm" onClick={() => setOpened(false)}>
              Отмена
            </Button>
            <Button
              color="red"
              size="compact-sm"
              className="press"
              loading={pending}
              onClick={() => onRemove(contact)}
            >
              Удалить
            </Button>
          </Group>
        </Stack>
      </Popover.Dropdown>
    </Popover>
  );
}

interface TableProps {
  donor: DonorFullCard;
  mayChange: boolean;
  removing: number | null;
  onRemove: (contact: ContactCard) => void;
}

function AddressTable({ donor, mayChange, removing, onRemove }: TableProps) {
  // Отметка нужна, когда выбирать есть из чего: при одном адресе она
  // повторяла бы очевидное.
  const marked = donor.contacts.length > 1 ? donor.letter_contact_id : null;
  return (
    // Таблица выступает на поле ячейки (`bleedTable`): адрес встаёт на ту же
    // левую линию, что заголовок раздела, а подсветка строки — шире текста.
    // Уже этого таблица уезжает в прокрутку: адрес, «данные регистратора»
    // одной строкой, две даты и значок удаления.
    <Table.ScrollContainer minWidth={680} type="native" className="scrollSlim bleedTable">
      <Table className="dataTable donorAddresses" verticalSpacing="sm" horizontalSpacing="sm">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Адрес</Table.Th>
            <Table.Th>Откуда</Table.Th>
            <Table.Th>Писали</Table.Th>
            <Table.Th>Отвечали</Table.Th>
            {mayChange && <Table.Th aria-label="Удалить" />}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {donor.contacts.map((contact) => (
            <Table.Tr key={contact.id}>
              {/* Текст — в своём элементе: ячейка во всю ширину колонки, и замер
                  по ней делил бы фон с его же переливом, а не буквы и фон. */}
              <Table.Td>
                <Group gap="xs" wrap="wrap">
                  <span className="donorEmail">{contact.email}</span>
                  {contact.id === marked && (
                    <Badge variant="light" size="sm" className="letterMark">
                      письмо уйдёт сюда
                    </Badge>
                  )}
                </Group>
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
              {mayChange && (
                <Table.Td>
                  <Group justify="center">
                    {contact.removal_refusal === null ? (
                      <RemoveAddress
                        contact={contact}
                        pending={removing === contact.id}
                        onRemove={onRemove}
                      />
                    ) : (
                      <KeptReason contact={contact} />
                    )}
                  </Group>
                </Table.Td>
              )}
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

/** Вписать адрес руками. Отказ сервера — под полем, его словами. */
function AddAddress({
  donor,
  onAdded,
}: {
  donor: DonorFullCard;
  onAdded: (card: DonorFullCard) => void;
}) {
  const [email, setEmail] = useState('');
  const add = useMutation({
    mutationFn: () => addDonorAddress(donor.id, email.trim()),
    onSuccess: (card) => {
      setEmail('');
      onAdded(card);
      notifications.show({ message: 'Адрес записан', color: 'green' });
    },
  });
  return (
    // Ряд переносится: на телефоне поле и кнопка вместе не помещаются, и
    // кнопка обрезалась краем карточки до «Добавить ‹» (снимок 26.09.2026).
    <Group gap="xs" align="flex-start" wrap="wrap" className="addAddress">
      <TextInput
        placeholder="editor@site.com"
        aria-label="Новый адрес почты"
        value={email}
        error={add.error ? refusalOf(add.error) : undefined}
        onChange={(event) => {
          setEmail(event.currentTarget.value);
          add.reset();
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && email.includes('@')) add.mutate();
        }}
        style={{ flex: '1 1 16rem', maxWidth: '22rem' }}
      />
      <Button
        variant="default"
        className="press"
        loading={add.isPending}
        disabled={!email.includes('@')}
        onClick={() => add.mutate()}
      >
        Добавить адрес
      </Button>
    </Group>
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

  // Адрес вписали или удалили — карточка приходит в ответе целиком, а списку
  // доноров и числу ждущих поиска пора перечитаться: адрес меняет и исход
  // поиска, и то, ждёт ли донор лестницу.
  const changed = useCallback(
    (card: DonorFullCard) => {
      queryClient.setQueryData(['donor', String(donor.id)], card);
      void queryClient.invalidateQueries({ queryKey: ['donors'] });
      void queryClient.invalidateQueries({ queryKey: CONTACTS_QUERY_KEY });
    },
    [queryClient, donor.id],
  );

  const remove = useMutation({
    mutationFn: (contact: ContactCard) => removeDonorAddress(donor.id, contact.id),
    onSuccess: (card, contact) => {
      changed(card);
      notifications.show({ message: `Адрес ${contact.email} удалён`, color: 'green' });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не удалили', message: refusalOf(failure), color: 'red' }),
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
  const mayChange = can('run');

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

        {!empty && (
          <AddressTable
            donor={donor}
            mayChange={mayChange}
            removing={remove.isPending ? (remove.variables?.id ?? null) : null}
            onRemove={(contact) => remove.mutate(contact)}
          />
        )}
        {/* Письмо не соберётся ни на один адрес — почему, словами сборки. */}
        {!empty && donor.letter_contact_id === null && donor.letter_blocked !== null && (
          <Text size="sm">{sentence(donor.letter_blocked)}</Text>
        )}

        {empty && donor.contact_refusal !== null && <Text size="sm">{donor.contact_refusal}</Text>}
        {mayStart && (
          <Stack gap="xs" align="flex-start">
            <Text size="sm">
              Лестница идёт от бесплатных ступеней к платной: почтовая запись, страницы сайта,
              данные регистратора и только потом платный сервис.
            </Text>
            {mayChange && (
              <Button className="press" loading={start.isPending} onClick={() => start.mutate()}>
                Найти адрес
              </Button>
            )}
          </Stack>
        )}
        {jobId !== null && <JobLine jobId={jobId} onFinished={finished} />}

        {/* Без права — объяснение на месте, одной строкой на все три действия:
            две похожие строки читались бы как сбой. */}
        {mayChange ? (
          <AddAddress donor={donor} onAdded={changed} />
        ) : (
          <Text size="sm" c="dimmed">
            {mayStart ? 'Ставить поиск, вписывать' : 'Вписывать'} и удалять адреса может сотрудник с
            правом «{PERMISSION_TITLES.run}».
          </Text>
        )}
      </Stack>
    </Card>
  );
}
