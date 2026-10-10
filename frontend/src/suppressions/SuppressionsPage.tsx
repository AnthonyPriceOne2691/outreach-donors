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
 *
 * **Таблица — с шириной колонок по самому длинному и прокруткой на узком
 * окне**: на телефоне в карточке без прокрутки значок причины ужимался до
 * «отписа…», а колонки правее не было видно вовсе (аудит 25.09.2026).
 *
 * **Одна карточка, запись — в окне, поиск — над таблицей** (аудит 09.10.2026): три
 * карточки и десяток строк пояснений стояли ради пустого списка, форма заведения
 * занимала экран всегда, хотя заводят редко, а поиска не было — при том что сюда
 * приходят с вопросом «почему не ушло письмо», а список поставщиков — сотни строк.
 * Числа в шапке — только не нулём: «Всего 0» и «Список пуст» говорили одно и то же.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Modal,
  SegmentedControl,
  Select,
  Stack,
  Table,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { IconPlus } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { addSuppression, listSuppressions, removeSuppression } from '../api/outreach';
import { SUPPRESSION_REASON_TITLES } from '../api/labels';
import type { StopEntry, SuppressionReason } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { PageHead } from '../components/PageHead';
import { Seams } from '../components/Seams';
import { formatDate } from '../format';
import { notify } from '../notices';

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

/** Колонки слева направо. Ширина первой — остаток: в ней домен или адрес.
 *  Остальные — по самому длинному, замеренному шрифтом экрана 25.09.2026:
 *  значок «пожаловался» — 102 px, дата — 75, значок «истёк 25.09.2026» —
 *  128, кнопка «Снять» — 60. Автор записи — адрес учётки, он переносится.
 *  Плюс 32 px полей ячейки и запас. */
const COLUMNS: { title: string; width?: string }[] = [
  { title: 'Кому не пишем' },
  { title: 'Причина', width: '9rem' },
  { title: 'Кто завёл', width: '12rem' },
  { title: 'Когда', width: '7.5rem' },
  { title: 'Срок', width: '10.5rem' },
];
const ACTIONS_WIDTH = '6rem';
const TABLE_MIN_WIDTH = 920;

interface TableProps {
  rows: StopEntry[];
  mayChange: boolean;
  onRemove: (row: StopEntry) => void;
}

/** Записи списка. Поля карточки вместе с полем ячейки — те же 32 px, что у шапки:
 *  текст начинается с одного места. */
function StopTable({ rows, mayChange, onRemove }: TableProps) {
  return (
    <Table.ScrollContainer minWidth={TABLE_MIN_WIDTH} type="native" className="scrollSlim">
      <Table
        className="dataTable fixedTable bleedTable"
        layout="fixed"
        tabularNums
        verticalSpacing="sm"
        horizontalSpacing="md"
      >
        <colgroup>
          {COLUMNS.map((column) => (
            <col key={column.title} style={column.width ? { width: column.width } : undefined} />
          ))}
          {mayChange ? <col style={{ width: ACTIONS_WIDTH }} /> : null}
        </colgroup>
        <Table.Thead>
          <Table.Tr>
            {COLUMNS.map((column) => (
              <Table.Th key={column.title}>{column.title}</Table.Th>
            ))}
            {mayChange ? <Table.Th /> : null}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((row) => (
            <Table.Tr key={row.id}>
              <Table.Td className="cellName">
                <Seams text={row.host ?? row.email ?? ''} />
              </Table.Td>
              <Table.Td>
                <Badge color={row.donor_decision ? 'red' : 'gray'} variant="light">
                  {SUPPRESSION_REASON_TITLES[row.reason]}
                </Badge>
              </Table.Td>
              <Table.Td className="wrapCell cellName">{row.created_by ?? '—'}</Table.Td>
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
                    className="press"
                    onClick={() => onRemove(row)}
                  >
                    Снять
                  </Button>
                </Table.Td>
              ) : null}
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

export function SuppressionsPage() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [target, setTarget] = useState('');
  const [reason, setReason] = useState<SuppressionReason>('manual');
  const [term, setTerm] = useState<Term>('forever');
  const [removing, setRemoving] = useState<StopEntry | null>(null);
  const [why, setWhy] = useState('');
  const [adding, setAdding] = useState(false);
  const [search, setSearch] = useState('');
  const [shownReason, setShownReason] = useState<string>('all');

  const { data, isLoading, error } = useQuery({
    queryKey: STOP_LIST_QUERY_KEY,
    queryFn: listSuppressions,
  });

  const add = useMutation({
    mutationFn: () => addSuppression({ target: target.trim(), reason, expires_at: endOf(term) }),
    onSuccess: async (row) => {
      await queryClient.invalidateQueries({ queryKey: STOP_LIST_QUERY_KEY });
      setTarget('');
      setAdding(false);
      notify({
        message: `${row.host ?? row.email} в стоп-листе — письма сняты с очереди`,
        color: 'green',
      });
    },
    onError: (failure) => notify({ title: 'Не завели', message: refusalOf(failure), color: 'red' }),
  });

  const take = useMutation({
    mutationFn: (row: StopEntry) => removeSuppression(row.id, why.trim() || null),
    onSuccess: async (row) => {
      await queryClient.invalidateQueries({ queryKey: STOP_LIST_QUERY_KEY });
      setRemoving(null);
      setWhy('');
      notify({
        message: `${row.host ?? row.email} снят со стоп-листа — новые письма ему снова уходят`,
        color: 'yellow',
      });
    },
    onError: (failure) => notify({ title: 'Не сняли', message: refusalOf(failure), color: 'red' }),
  });

  if (isLoading) return <Loader aria-label="Загружаем стоп-лист" m="md" />;
  if (error) {
    return (
      <Alert color="red" title="Стоп-лист не загрузился" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }

  const all = data?.rows ?? [];
  const mayChange = can('send');
  const needsWhy = removing?.donor_decision === true;
  // Поиск и причина — по уже пришедшим строкам: список приходит целиком.
  const needle = search.trim().toLowerCase();
  const rows = all.filter(
    (row) =>
      (shownReason === 'all' || row.reason === shownReason) &&
      (needle === '' || (row.host ?? row.email ?? '').toLowerCase().includes(needle)),
  );
  const reasons = [...new Set(all.map((row) => row.reason))];
  const facts = [
    data?.total ? `всего ${data.total}` : null,
    data?.donor_decisions ? `по решению адресата ${data.donor_decisions}` : null,
    data?.expired ? `истекли и больше не держат ${data.expired}` : null,
  ].filter((fact) => fact !== null);

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          {/* Кнопка — сразу за заголовком, а не в правом краю строки: там она стояла
              отдельно от всего, через полэкрана пустоты (замечание Anthony 10.10.2026). */}
          <Group gap="md" align="center">
            <PageHead
              title="Стоп-лист"
              hint="Кому мы не пишем ни на одном этапе. Проверяется дважды: при отборе доменов — домен из списка в прогон не идёт и юнитов на него не тратится, — и перед каждой отправкой, так что письмо адресату из списка не уйдёт, даже если его собрали раньше."
            />
            {mayChange ? (
              <Button
                className="press"
                leftSection={<IconPlus size={16} />}
                onClick={() => setAdding(true)}
              >
                Добавить…
              </Button>
            ) : null}
          </Group>
          {facts.length > 0 && <Text size="sm">{facts.join(' · ')}</Text>}

          {all.length === 0 ? (
            <Text size="sm" c="dimmed">
              Список пуст. Сюда попадают те, кто отписался или пожаловался, и те, кого внесли
              руками.
            </Text>
          ) : (
            <>
              <Group gap="sm">
                <TextInput
                  size="xs"
                  placeholder="Домен или адрес"
                  aria-label="Поиск по домену или адресу"
                  value={search}
                  onChange={(event) => setSearch(event.currentTarget.value)}
                  w="20rem"
                />
                <Select
                  size="xs"
                  aria-label="Причина записи"
                  allowDeselect={false}
                  value={shownReason}
                  onChange={(value) => setShownReason(value ?? 'all')}
                  data={[
                    { value: 'all', label: 'все причины' },
                    ...reasons.map((value) => ({
                      value,
                      label: SUPPRESSION_REASON_TITLES[value],
                    })),
                  ]}
                  w="10rem"
                />
              </Group>
              {rows.length === 0 ? (
                <Text size="sm" c="dimmed">
                  Под поиск ничего не попало — в стоп-листе такого адреса нет.
                </Text>
              ) : (
                <StopTable
                  rows={rows}
                  mayChange={mayChange}
                  onRemove={(row) => {
                    setRemoving(row);
                    setWhy('');
                  }}
                />
              )}
            </>
          )}
        </Stack>
      </Card>

      <Modal opened={adding} onClose={() => setAdding(false)} title="Больше не писать">
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            Домен закрывает сайт целиком, адрес — один ящик. Домен, которого ещё нет в базе,
            заводится вместе с записью: список поставщиков приходит раньше первого прогона.
          </Text>
          <TextInput
            label="Домен или адрес"
            placeholder="site.com или editor@site.com"
            value={target}
            onChange={(event) => setTarget(event.currentTarget.value)}
            data-autofocus
          />
          {/* Два значения — переключателем, а не списком в 200 px (аудит 09.10.2026). */}
          <Stack gap={4}>
            <Text size="sm" fw={500}>
              Причина
            </Text>
            <SegmentedControl
              aria-label="Причина"
              data={HAND_REASONS}
              value={reason}
              onChange={(picked) => setReason(picked as SuppressionReason)}
              style={{ alignSelf: 'flex-start' }}
            />
          </Stack>
          <Stack gap={4}>
            <Text size="sm" fw={500}>
              Держит
            </Text>
            <SegmentedControl
              aria-label="Держит"
              data={TERMS.map((item) => ({ value: item.value, label: item.label }))}
              value={term}
              onChange={(picked) => setTerm(picked as Term)}
              style={{ alignSelf: 'flex-start' }}
            />
          </Stack>
          <Group justify="flex-end" gap="sm">
            <Button variant="default" onClick={() => setAdding(false)}>
              Отмена
            </Button>
            <Button
              onClick={() => add.mutate()}
              loading={add.isPending}
              disabled={target.trim().length < 3}
            >
              Больше не писать
            </Button>
          </Group>
        </Stack>
      </Modal>

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
