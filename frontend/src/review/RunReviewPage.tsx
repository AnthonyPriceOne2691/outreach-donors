/**
 * Рассмотрение прогона: принять или отклонить предложенных доноров.
 *
 * **Прогон кончается очередью, а не базой.** Пороги отвечают «годен ли
 * по цифрам», человек — «берём ли»: 23.09 пороги признали годными
 * microsoft.com и nih.gov. Контакты и письма получают только принятые.
 *
 * **Судья сортирует, а не решает.** Сверху — кому он советует принять,
 * сомнительные скрыты под переключателем со счётчиком: судья ошибается,
 * и отказ модели без глаз человека стоил бы донора.
 *
 * **Счёт «судья против человека» — наверху экрана.** По нему решают,
 * когда судье можно доверить приём: совет «принять» верен в 95% на 200
 * решениях. До этого приём — только руками.
 */

import {
  Alert,
  Button,
  Card,
  Checkbox,
  Group,
  Loader,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Switch,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useParams } from 'react-router-dom';

import { REVIEW_DECISIONS } from '../api/labels';
import { decideCandidates, loadAccuracy, loadReview } from '../api/review';
import type { AccuracyView, ReviewDecision } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { Metric } from '../components/Metric';
import { CandidateRow } from './CandidateRow';

const STATUSES = Object.keys(REVIEW_DECISIONS) as ReviewDecision[];

const EMPTY: Record<ReviewDecision, string> = {
  pending: 'Предложенных нет: всё рассмотрено или скрыто как сомнительное.',
  accepted: 'Принятых пока нет.',
  rejected: 'Отклонённых нет.',
};

function percent(value: number | null): string {
  return value === null ? '—' : `${Math.round(value * 100)}%`;
}

/** Точность советов судьи и готовность к автоприёму — словами и числами. */
function JudgeScore({ accuracy }: { accuracy: AccuracyView }) {
  const accept = accuracy.by_advice.accept;
  const reject = accuracy.by_advice.reject;
  const need = `≥ ${Math.round(accuracy.auto_accept_precision * 100)}% на ≥ ${accuracy.auto_accept_min_decisions}`;
  return (
    <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="sm">
      <Metric
        title="Решено человеком"
        value={accuracy.decided}
        hint={`судья просил посмотреть: ${accuracy.asked_to_review}`}
      />
      <Metric
        title="Совет «площадка» верен"
        value={percent(accept?.precision ?? null)}
        hint={accept ? `${accept.agreed} из ${accept.advised}` : 'решений нет'}
      />
      <Metric
        title="Совет «не площадка» верен"
        value={percent(reject?.precision ?? null)}
        hint={reject ? `${reject.agreed} из ${reject.advised}` : 'решений нет'}
      />
      <Metric
        title="Автоприём по судье"
        value={accuracy.auto_accept_ready ? 'можно' : 'рано'}
        hint={`нужно ${need}`}
        color={accuracy.auto_accept_ready ? 'green' : undefined}
      />
    </SimpleGrid>
  );
}

export function RunReviewPage() {
  const { can } = useSession();
  const mayDecide = can('prices');
  const runId = Number(useParams().id);
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<ReviewDecision>('pending');
  const [showDoubtful, setShowDoubtful] = useState(false);
  const [picked, setPicked] = useState<Set<number>>(new Set());

  const review = useQuery({
    queryKey: ['review', runId, status, showDoubtful],
    queryFn: () => loadReview(runId, status, showDoubtful),
  });
  const accuracy = useQuery({
    queryKey: ['review-accuracy'],
    queryFn: () => loadAccuracy(),
  });

  const decide = useMutation({
    mutationFn: ({ ids, decision }: { ids: number[]; decision: ReviewDecision }) =>
      decideCandidates(runId, ids, decision),
    onSuccess: async (result, { decision }) => {
      setPicked(new Set());
      await queryClient.invalidateQueries({ queryKey: ['review', runId] });
      await queryClient.invalidateQueries({ queryKey: ['review-accuracy'] });
      await queryClient.invalidateQueries({ queryKey: ['runs'] });
      notifications.show({
        color: 'green',
        message:
          decision === 'accepted'
            ? `Принято: ${result.accepted}. Поиск контактов поставлен в очередь`
            : `${REVIEW_DECISIONS[decision].title}: ${result.changed}`,
      });
    },
    onError: (failure) =>
      notifications.show({
        color: 'red',
        title: 'Решение не сохранено',
        message: failure instanceof Error ? failure.message : 'Сервер отказал без объяснения',
      }),
  });

  const switchTo = (next: ReviewDecision) => {
    setPicked(new Set());
    setStatus(next);
  };

  if (review.isLoading) return <Loader />;
  if (review.error || !review.data) {
    return (
      <Alert color="red" title="Очередь не загрузилась">
        {review.error instanceof Error ? review.error.message : 'Сервер не ответил'}
      </Alert>
    );
  }

  const view = review.data;
  const rows = view.rows;
  const allPicked = rows.length > 0 && rows.every((row) => picked.has(row.candidate_id));
  const toggle = (id: number, on: boolean) =>
    setPicked((was) => {
      const next = new Set(was);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  const bulk = (decision: ReviewDecision) => decide.mutate({ ids: [...picked], decision });

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <Stack gap={6}>
            <Title order={3}>Прогон №{view.run.id}: рассмотрение</Title>
            <Text size="sm" c="dimmed" maw={720}>
              {view.run.country.toUpperCase()}, ключей {view.run.keywords}. Годные по порогам ждут
              решения: контакты ищутся и письма собираются только принятым. Судья сортирует очередь
              и подсказывает, но не решает; сомнительные скрыты, а не выброшены.
            </Text>
          </Stack>
          {accuracy.data && <JudgeScore accuracy={accuracy.data} />}
        </Stack>
      </Card>

      <Card className="glassPanel" p="xl">
        <Stack gap="md">
          <SegmentedControl
            value={status}
            onChange={(value) => switchTo(value as ReviewDecision)}
            data={STATUSES.map((value) => ({
              value,
              label: `${REVIEW_DECISIONS[value].title} — ${view.counts[value]}`,
            }))}
          />
          <Group justify="space-between" align="center">
            {status === 'pending' ? (
              <Switch
                label={`Показать сомнительные (скрыто ${view.hidden})`}
                checked={showDoubtful}
                onChange={(event) => {
                  setPicked(new Set());
                  setShowDoubtful(event.currentTarget.checked);
                }}
              />
            ) : (
              <span />
            )}
            {mayDecide && picked.size > 0 && (
              <Group gap="sm">
                <Text size="sm">Выбрано: {picked.size}</Text>
                {status === 'pending' ? (
                  <>
                    <Button
                      color="green"
                      loading={decide.isPending}
                      onClick={() => bulk('accepted')}
                    >
                      Принять выбранные
                    </Button>
                    <Button
                      variant="default"
                      loading={decide.isPending}
                      onClick={() => bulk('rejected')}
                    >
                      Отклонить выбранные
                    </Button>
                  </>
                ) : (
                  <Button
                    variant="default"
                    loading={decide.isPending}
                    onClick={() => bulk('pending')}
                  >
                    Вернуть выбранные
                  </Button>
                )}
              </Group>
            )}
          </Group>
        </Stack>
      </Card>

      <Card className="glass" p="xs">
        {rows.length === 0 ? (
          <Text size="sm" c="dimmed" p="lg">
            {EMPTY[status]}
          </Text>
        ) : (
          <Table.ScrollContainer minWidth={1100}>
            <Table className="dataTable" verticalSpacing="sm" horizontalSpacing="md">
              <Table.Thead>
                <Table.Tr>
                  {mayDecide && (
                    <Table.Th>
                      <Checkbox
                        aria-label="Выбрать все на экране"
                        checked={allPicked}
                        onChange={(event) =>
                          setPicked(
                            event.currentTarget.checked
                              ? new Set(rows.map((row) => row.candidate_id))
                              : new Set(),
                          )
                        }
                      />
                    </Table.Th>
                  )}
                  <Table.Th>Домен</Table.Th>
                  <Table.Th>Нашёлся по ключам</Table.Th>
                  <Table.Th>Судья</Table.Th>
                  <Table.Th>Донор ответил</Table.Th>
                  <Table.Th>Решение</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((row) => (
                  <CandidateRow
                    key={row.candidate_id}
                    row={row}
                    mayDecide={mayDecide}
                    busy={decide.isPending}
                    checked={picked.has(row.candidate_id)}
                    onCheck={(on) => toggle(row.candidate_id, on)}
                    onDecide={(decision) => decide.mutate({ ids: [row.candidate_id], decision })}
                  />
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Card>
    </Stack>
  );
}
