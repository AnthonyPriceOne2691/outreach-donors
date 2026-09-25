/**
 * Ручная проверка кандидатов в рекламодатели.
 *
 * **Экран существует ради одного числа.** Требование ограничивает долю
 * ложных рекламодателей десятью процентами, а скоринг судит по пяти
 * признакам, два из которых выведены из замера на одной нише. Без
 * человека, который смотрит пограничные, эта доля не измерена
 * и не удержана.
 *
 * **Причины показываются целиком, а не прячутся за баллом.** Человек
 * решает не по числу «3», а по тому, из чего оно сложилось: «ссылки
 * с двадцати страниц донора» и «коммерческий анкор под nofollow» — это
 * разные основания, и одно из них бывает ошибкой.
 *
 * **Страница и анкор — рядом с решением.** Письмо пишется под конкретную
 * найденную ссылку, и человек должен видеть ту самую, по которой оно
 * будет написано, а не догадываться о ней.
 *
 * **Счётчики по всем вердиктам, включая отсеянных.** По ним видно, что
 * список «кому не пишем» работает, а не молчит.
 *
 * **Числа и текст сходятся.** «Спорно: 1» над «Спорных нет» читалось как
 * сбой: спорный был, но решённый, и прятался за выключенным «Показывать
 * решённые» без объяснения (аудит 25.09.2026). Пустая таблица теперь
 * говорит, сколько решено и где их видно.
 */

import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Stack,
  Switch,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { decideCandidate, fetchCandidates } from '../api/advertisers';
import type { CandidateCard } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { formatNumber, plural } from '../format';

const QUERY_KEY = ['advertisers'] as const;

/** Как называется каждый исход скоринга на человеческом языке. */
const VERDICTS: Record<CandidateCard['verdict'], { label: string; color: string }> = {
  bought: { label: 'куплена', color: 'green' },
  pending: { label: 'спорно', color: 'yellow' },
  skipped: { label: 'мимо', color: 'gray' },
  blocked: { label: 'кому не пишем', color: 'gray' },
};

/** Колонки слева направо. Ширина первой — остаток: в ней рекламодатель.
 *  «За что» и «Ссылка» — текст, он переносится; остальные — по самому
 *  длинному шрифтом экрана (25.09.2026): кнопки «Пишем» и «Не пишем» с
 *  зазором — около 154 px. Плюс 32 px полей ячейки и запас. */
const COLUMNS: { title: string; width?: string }[] = [
  { title: 'Кому ссылается' },
  { title: 'Донор', width: '11rem' },
  { title: 'Балл', width: '4.75rem' },
  { title: 'За что', width: '15rem' },
  { title: 'Ссылка', width: '12rem' },
];
const ACTIONS_WIDTH = '12.5rem';
const TABLE_MIN_WIDTH = 1080;

/** Почему таблица пуста — словами, и с теми же числами, что в шапке. */
function EmptyQueue({
  decided,
  onShowDecided,
}: {
  decided: number;
  onShowDecided: (() => void) | null;
}) {
  if (decided > 0 && onShowDecided !== null) {
    return (
      <Stack gap={6} align="flex-start">
        <Text size="sm" fw={500}>
          Все спорные решены — {formatNumber(decided)}.
        </Text>
        <Text size="sm" c="dimmed">
          Решённые скрыты, пока выключено «Показывать решённые»: по ним уже сказали «пишем» или «не
          пишем».
        </Text>
        <Button variant="subtle" size="compact-sm" className="press" onClick={onShowDecided}>
          Показать решённые
        </Button>
      </Stack>
    );
  }
  return (
    <Text size="sm" c="dimmed">
      Спорных нет. Сюда попадают ссылки, набравшие 2–3 балла: одного признака хватило, чтобы
      заподозрить размещение, и не хватило, чтобы решить за человека.
    </Text>
  );
}

export function AdvertisersPage() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [includeDecided, setIncludeDecided] = useState(false);

  const query = useQuery({
    queryKey: [...QUERY_KEY, includeDecided],
    queryFn: () => fetchCandidates(includeDecided),
    // Переключатель не перерисовывает экран: прежняя таблица стоит
    // приглушённой, пока не придёт новая. Раньше экран целиком сменялся
    // значком загрузки — «как будто страница загружается заново».
    placeholderData: keepPreviousData,
  });
  const { data, isLoading, error } = query;
  const stale = query.isPlaceholderData;

  const decide = useMutation({
    mutationFn: ({ row, confirmed }: { row: CandidateCard; confirmed: boolean }) =>
      decideCandidate(row.id, confirmed, row.confirmed !== null),
    onSuccess: async (row) => {
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
      notifications.show({
        message: row.confirmed ? `${row.target_root}: пишем ему` : `${row.target_root}: не пишем`,
        color: 'green',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не записали', message: refusalOf(failure), color: 'red' }),
  });

  if (isLoading) return <Loader aria-label="Загружаем очередь проверки" m="md" />;
  if (error && data === undefined) {
    return (
      <Alert color="red" title="Очередь не загрузилась" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }

  const rows = data?.rows ?? [];
  const counts = data?.counts ?? {};
  const waiting = data?.waiting ?? 0;
  // Спорные, по которым человек уже решил: счётчик «спорно» считает всех,
  // «ждут человека» — только нерешённых.
  const decided = Math.max(0, (counts['pending'] ?? 0) - waiting);
  const mayDecide = can('prices');

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="sm">
          <Title order={3}>Рекламодатели: спорные</Title>
          <Text size="sm" c="dimmed" maw={720}>
            Скоринг разложил найденные ссылки на три кучи: куплена, мимо и спорно. Спорные — здесь.
            Решение человека сильнее вердикта скоринга и балл не переписывает: по расхождению между
            ними и видно, как часто скоринг ошибается.
          </Text>
          <Group gap="xs">
            {(['bought', 'pending', 'skipped', 'blocked'] as const).map((verdict) => (
              <Badge key={verdict} variant="light" color={VERDICTS[verdict].color}>
                {VERDICTS[verdict].label}: {formatNumber(counts[verdict] ?? 0)}
              </Badge>
            ))}
          </Group>
          <Group justify="space-between" align="center">
            <Text size="sm">
              Ждут человека: <b>{formatNumber(waiting)}</b>
              {decided > 0 ? (
                <>
                  , решено: <b>{formatNumber(decided)}</b>
                </>
              ) : null}
            </Text>
            <Switch
              size="sm"
              label="Показывать решённые"
              checked={includeDecided}
              onChange={(event) => setIncludeDecided(event.currentTarget.checked)}
            />
          </Group>
        </Stack>
      </Card>

      {/* Поля карточки с таблицей — вместе с полем ячейки те же 32 px, что
          у панели сверху: текст соседних карточек начинается с одного места. */}
      <Card
        className="glassPanel staleRows"
        p={rows.length === 0 ? 'xl' : 'md'}
        data-stale={stale || undefined}
        aria-busy={stale || undefined}
        inert={stale}
      >
        {error ? (
          <Alert color="red" title="Очередь не загрузилась">
            {refusalOf(error)}
          </Alert>
        ) : rows.length === 0 ? (
          <EmptyQueue
            decided={decided}
            onShowDecided={includeDecided ? null : () => setIncludeDecided(true)}
          />
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
                {mayDecide ? <col style={{ width: ACTIONS_WIDTH }} /> : null}
              </colgroup>
              <Table.Thead>
                <Table.Tr>
                  {COLUMNS.map((column) => (
                    <Table.Th key={column.title}>{column.title}</Table.Th>
                  ))}
                  {mayDecide ? <Table.Th /> : null}
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((row) => (
                  <Table.Tr key={row.id}>
                    <Table.Td>
                      {/* Имя — чернилами, как во всех таблицах: бирюзовая
                          ссылка на бирюзовом углу полотна не держит норму. */}
                      <Anchor
                        href={`https://${row.target_root}`}
                        target="_blank"
                        rel="noreferrer"
                        c="var(--ink)"
                        fw={500}
                        underline="hover"
                        className="cellName"
                      >
                        {row.target_root}
                      </Anchor>
                      {row.confirmed !== null ? (
                        <Text size="xs" c="dimmed" className="cellName">
                          {row.confirmed ? 'пишем' : 'не пишем'} — {row.decided_by}
                        </Text>
                      ) : null}
                    </Table.Td>
                    <Table.Td className="wrapCell cellName">{row.donor_host}</Table.Td>
                    <Table.Td>
                      <Badge variant="light" color={VERDICTS[row.verdict].color}>
                        {row.points}
                      </Badge>
                    </Table.Td>
                    {/* Причины — строками по центру, как всё в колонке, без
                        маркеров списка: маркеры стояли у левого края, а текст
                        посередине, и строки читались оторванными от них. */}
                    <Table.Td className="wrapCell">
                      <Stack gap={2}>
                        {row.reasons.map((reason) => (
                          <Text key={reason} size="xs">
                            {reason}
                          </Text>
                        ))}
                        <Text size="xs" c="dimmed">
                          {formatNumber(row.links)}{' '}
                          {plural(row.links, 'ссылка', 'ссылки', 'ссылок')} с{' '}
                          {formatNumber(row.pages)}{' '}
                          {plural(row.pages, 'страницы', 'страниц', 'страниц')}
                        </Text>
                      </Stack>
                    </Table.Td>
                    <Table.Td className="wrapCell">
                      {row.best_page_url ? (
                        <Anchor href={row.best_page_url} target="_blank" rel="noreferrer" size="sm">
                          страница
                        </Anchor>
                      ) : (
                        '—'
                      )}
                      {row.best_anchor ? (
                        <Text size="xs" c="dimmed" className="cellName">
                          анкор: {row.best_anchor}
                        </Text>
                      ) : null}
                    </Table.Td>
                    {mayDecide ? (
                      <Table.Td>
                        <Group gap="xs" justify="center" wrap="nowrap">
                          <Button
                            size="compact-sm"
                            className="press"
                            loading={decide.isPending && decide.variables?.row.id === row.id}
                            onClick={() => decide.mutate({ row, confirmed: true })}
                          >
                            Пишем
                          </Button>
                          <Button
                            size="compact-sm"
                            variant="subtle"
                            color="gray"
                            className="press"
                            onClick={() => decide.mutate({ row, confirmed: false })}
                          >
                            Не пишем
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
    </Stack>
  );
}
