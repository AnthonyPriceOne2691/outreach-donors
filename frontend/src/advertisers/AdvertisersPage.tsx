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
 */

import {
  Alert,
  Anchor,
  Badge,
  Button,
  Card,
  Group,
  List,
  Loader,
  Stack,
  Switch,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { decideCandidate, fetchCandidates } from '../api/advertisers';
import type { CandidateCard } from '../api/types';
import { useSession } from '../auth/AuthProvider';

const QUERY_KEY = ['advertisers'] as const;

/** Как называется каждый исход скоринга на человеческом языке. */
const VERDICTS: Record<CandidateCard['verdict'], { label: string; color: string }> = {
  bought: { label: 'куплена', color: 'green' },
  pending: { label: 'спорно', color: 'yellow' },
  skipped: { label: 'мимо', color: 'gray' },
  blocked: { label: 'кому не пишем', color: 'gray' },
};

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

export function AdvertisersPage() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const [includeDecided, setIncludeDecided] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: [...QUERY_KEY, includeDecided],
    queryFn: () => fetchCandidates(includeDecided),
  });

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
  if (error) {
    return (
      <Alert color="red" title="Очередь не загрузилась" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }

  const rows = data?.rows ?? [];
  const counts = data?.counts ?? {};
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
                {VERDICTS[verdict].label}: {counts[verdict] ?? 0}
              </Badge>
            ))}
          </Group>
          <Group justify="space-between" align="center">
            <Text size="sm">
              Ждут человека: <b>{data?.waiting ?? 0}</b>
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

      <Card className="glassPanel" p="xl">
        {rows.length === 0 ? (
          <Text size="sm" c="dimmed">
            Спорных нет. Сюда попадают ссылки, набравшие 2–3 балла: одного признака хватило, чтобы
            заподозрить размещение, и не хватило, чтобы решить за человека.
          </Text>
        ) : (
          <Table className="dataTable" verticalSpacing="sm" horizontalSpacing="md" miw={900}>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Кому ссылается</Table.Th>
                <Table.Th>Донор</Table.Th>
                <Table.Th>Балл</Table.Th>
                <Table.Th>За что</Table.Th>
                <Table.Th>Ссылка</Table.Th>
                {mayDecide ? <Table.Th /> : null}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((row) => (
                <Table.Tr key={row.id}>
                  <Table.Td>
                    <Anchor href={`https://${row.target_root}`} target="_blank" rel="noreferrer">
                      {row.target_root}
                    </Anchor>
                    {row.confirmed !== null ? (
                      <Text size="xs" c="dimmed">
                        {row.confirmed ? 'пишем' : 'не пишем'} — {row.decided_by}
                      </Text>
                    ) : null}
                  </Table.Td>
                  <Table.Td>{row.donor_host}</Table.Td>
                  <Table.Td>
                    <Badge variant="light" color={VERDICTS[row.verdict].color}>
                      {row.points}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <List size="xs" spacing={2} withPadding={false}>
                      {row.reasons.map((reason) => (
                        <List.Item key={reason}>{reason}</List.Item>
                      ))}
                    </List>
                    <Text size="xs" c="dimmed">
                      ссылок {row.links} с {row.pages} страниц
                    </Text>
                  </Table.Td>
                  <Table.Td>
                    {row.best_page_url ? (
                      <Anchor href={row.best_page_url} target="_blank" rel="noreferrer" size="sm">
                        страница
                      </Anchor>
                    ) : (
                      '—'
                    )}
                    {row.best_anchor ? (
                      <Text size="xs" c="dimmed">
                        анкор: {row.best_anchor}
                      </Text>
                    ) : null}
                  </Table.Td>
                  {mayDecide ? (
                    <Table.Td>
                      <Group gap="xs" justify="flex-end" wrap="nowrap">
                        <Button
                          size="compact-sm"
                          loading={decide.isPending && decide.variables?.row.id === row.id}
                          onClick={() => decide.mutate({ row, confirmed: true })}
                        >
                          Пишем
                        </Button>
                        <Button
                          size="compact-sm"
                          variant="subtle"
                          color="gray"
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
        )}
      </Card>
    </Stack>
  );
}
