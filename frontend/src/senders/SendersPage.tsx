/**
 * Домены рассылки: кто сейчас может отправлять и на каком разгоне.
 *
 * Экран сгруппирован по доменам, а не по ящикам: репутация живёт
 * у домена, а ящики на нём — это просто пропускная способность.
 * Человек решает «включить домен», а не «включить третий ящик».
 *
 * **Включённый заново домен начинает разгон с начала** — об этом
 * сказано прямо на кнопке, а не в документации: домен выключают обычно
 * потому, что с ним что-то не так, и вернуть его сразу на полный кап
 * значит добить репутацию, которая и так пошатнулась.
 */

import { Alert, Card, Loader, Stack, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { disableSender, enableSender, listSenders } from '../api/outreach';
import type { SenderCard } from '../api/types';
import { SenderCard as DomainCard } from './SenderCard';
import type { DomainGroup } from './SenderCard';

const SENDERS_QUERY_KEY = ['senders'] as const;

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

function groupByDomain(senders: SenderCard[]): DomainGroup[] {
  const groups = new Map<string, SenderCard[]>();
  for (const sender of senders) {
    groups.set(sender.domain, [...(groups.get(sender.domain) ?? []), sender]);
  }
  return [...groups.entries()].map(([domain, boxes]) => ({
    domain,
    boxes,
    enabled: boxes.some((box) => box.enabled),
    sentToday: boxes.reduce((sum, box) => sum + box.sent_today, 0),
    allowance: boxes.reduce((sum, box) => sum + box.warmup_allowance, 0),
  }));
}

export function SendersPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: SENDERS_QUERY_KEY,
    queryFn: listSenders,
  });

  const switchDomain = useMutation({
    mutationFn: async ({ group, on }: { group: DomainGroup; on: boolean }) => {
      for (const box of group.boxes) {
        await (on ? enableSender(box.id) : disableSender(box.id, 'выключено вручную'));
      }
      return { group, on };
    },
    onSuccess: async ({ group, on }) => {
      await queryClient.invalidateQueries({ queryKey: SENDERS_QUERY_KEY });
      notifications.show({
        message: on
          ? `${group.domain} включён — разгон начался заново`
          : `${group.domain} выключен, начатые цепочки не рвутся`,
        color: on ? 'green' : 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не переключили', message: refusalOf(failure), color: 'red' }),
  });

  if (isLoading) return <Loader aria-label="Загружаем домены рассылки" m="md" />;
  if (error) {
    return (
      <Alert color="red" title="Домены не загрузились" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }

  const groups = groupByDomain(data?.senders ?? []);
  const enabledDomains = groups.filter((group) => group.enabled).length;

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="sm">
          <Title order={3}>Домены рассылки</Title>
          <Text size="sm" c="dimmed" maw={640}>
            Репутация живёт у домена, поэтому включают и выключают домен целиком. Выключенный не
            получает новых писем, но начатые цепочки не рвутся: письмо, отправленное вчера, ждёт
            ответа.
          </Text>
          {enabledDomains === 0 ? (
            <Alert color="yellow" title="Отправлять нечем">
              Все домены выключены. Новые письма не уйдут, пока хотя бы один не включат — и он
              начнёт с начала разгона.
            </Alert>
          ) : (
            <Text size="sm">
              Отправлять могут <b>{enabledDomains}</b> из {groups.length} доменов.
            </Text>
          )}
        </Stack>
      </Card>

      <Stack gap="sm">
        {groups.map((group) => (
          <DomainCard
            key={group.domain}
            group={group}
            busy={switchDomain.isPending && switchDomain.variables?.group.domain === group.domain}
            onSwitch={(on) => switchDomain.mutate({ group, on })}
          />
        ))}
      </Stack>
    </Stack>
  );
}
