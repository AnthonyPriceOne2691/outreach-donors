/**
 * «К письму»: перевод «куплено» и «пишем» в рекламодатели и поиск им адресов.
 *
 * **Раньше это была только консоль** (`outreach advertisers-promote --contacts`,
 * до 06.10.2026): кандидатов человек решал кнопками, а довести их до письма
 * без инженера не мог.
 *
 * **Числа над кнопкой — те, кого она переведёт.** Сервер считает их тем же
 * условием, что и сам перевод: «куплено» и «пишем», без «не пишем». Стоп-листы
 * вычитает перевод и называет отсеянных в итоге.
 *
 * **Адреса ищутся сами**, как у принятых доноров Этапа 1: человек, сказавший
 * «переводим», собирается им писать. Строка «ждут адреса · найти» — та же,
 * что у доноров (`usePendingContacts`), с запросами рекламодателей.
 */

import { Alert, Anchor, Badge, Button, Card, Group, Stack, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import type { PromoteResult } from '../api/advertisers';
import {
  fetchAdvertiserContacts,
  fetchPromotion,
  promoteAdvertisers,
  searchAdvertiserContacts,
} from '../api/advertisers';
import { refusalOf } from '../api/client';
import { useSession } from '../auth/AuthProvider';
import type { ContactsSource } from '../donors/PendingContacts';
import { usePendingContacts } from '../donors/PendingContacts';
import { formatNumber } from '../format';

const PROMOTION_QUERY_KEY = ['advertisers-promotion'] as const;

const ADVERTISER_CONTACTS: ContactsSource = {
  queryKey: ['advertiser-contacts'],
  fetchState: fetchAdvertiserContacts,
  search: searchAdvertiserContacts,
  refresh: [PROMOTION_QUERY_KEY],
};

/** Итог перевода одной строкой: что завели, что отсеяли, ищем ли адреса. */
export function promotedLine(result: PromoteResult): string {
  const count = (key: string) => result.report[key] ?? 0;
  if ((result.report['рассмотрено'] ?? 0) === 0) return 'Переводить некого.';
  const parts = [
    `заведено ${formatNumber(count('заведено'))}`,
    `обновлено ${formatNumber(count('обновлено'))}`,
  ];
  const dropped = count('донор в стоп-листе поставщиков') + count('адресат в общем стоп-листе');
  if (dropped > 0) parts.push(`отсеяно стоп-листами ${formatNumber(dropped)}`);
  const removed = count('снято с уже заведённых');
  if (removed > 0) parts.push(`снято с заведённых ${formatNumber(removed)}`);
  const tail =
    result.pending > 0 ? `ищем адреса: ${formatNumber(result.pending)}` : 'адреса есть у всех';
  return `Перевод: ${parts.join(', ')}; ${tail}.`;
}

export function PromotePanel() {
  const { can } = useSession();
  const queryClient = useQueryClient();
  const contacts = usePendingContacts(ADVERTISER_CONTACTS);
  const [outcome, setOutcome] = useState<PromoteResult | null>(null);
  const { data, error } = useQuery({ queryKey: PROMOTION_QUERY_KEY, queryFn: fetchPromotion });

  const promote = useMutation({
    mutationFn: promoteAdvertisers,
    onSuccess: async (result) => {
      setOutcome(result);
      if (result.contacts_job_id !== null) contacts.follow(result.contacts_job_id);
      await queryClient.invalidateQueries({ queryKey: PROMOTION_QUERY_KEY });
      await queryClient.invalidateQueries({ queryKey: ADVERTISER_CONTACTS.queryKey });
      notifications.show({ message: promotedLine(result), color: 'green' });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не перевели', message: refusalOf(failure), color: 'red' }),
  });

  if (data === undefined) {
    // Пропавшая молча панель читалась бы как «перевода нет», а не «не загрузилось».
    return error ? (
      <Alert color="red" title="«К письму» не загрузилось">
        {refusalOf(error)}
      </Alert>
    ) : null;
  }

  return (
    <Card className="glassPanel" p="xl">
      <Stack gap="sm">
        <Title order={4}>К письму</Title>
        <Text size="sm" c="dimmed" maw={720}>
          Перевод берёт «куплено» и тех, кому сказали «Пишем», — по одному рекламодателю на домен,
          под его лучшую ссылку. Адреса им ищутся сами, той же лестницей, что донорам. Офферы
          собираются в{' '}
          <Anchor component={Link} to="/letters">
            «Письмах»
          </Anchor>{' '}
          на переключателе «Рекламодателям».
        </Text>
        <Group gap="xs">
          <Badge variant="light" color={data.fresh > 0 ? 'green' : 'gray'}>
            к переводу: {formatNumber(data.ready)}, новых {formatNumber(data.fresh)}
          </Badge>
          <Badge variant="light" color="gray">
            рекламодателей: {formatNumber(data.advertisers)}, с адресом{' '}
            {formatNumber(data.with_address)}
          </Badge>
        </Group>
        <Group gap="md" align="center">
          {can('run') ? (
            <Button
              className="press"
              loading={promote.isPending}
              disabled={data.ready === 0}
              onClick={() => promote.mutate()}
            >
              Перевести в рекламодатели
            </Button>
          ) : null}
          {contacts.control}
        </Group>
        {data.ready === 0 ? (
          <Text size="sm" c="dimmed">
            Переводить некого: в «куплено» пусто, и «Пишем» спорным пока никто не сказал.
          </Text>
        ) : null}
        {outcome !== null ? <Text size="sm">{promotedLine(outcome)}</Text> : null}
        {contacts.outcome}
      </Stack>
    </Card>
  );
}
