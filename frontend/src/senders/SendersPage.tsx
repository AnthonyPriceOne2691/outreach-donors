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

import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Progress,
  Stack,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { disableSender, enableSender, listSenders } from '../api/outreach';
import type { SenderCard } from '../api/types';

const SENDERS_QUERY_KEY = ['senders'] as const;

function refusalOf(error: unknown): string {
  return error instanceof Error ? error.message : 'Сервер отказал без объяснения';
}

interface DomainGroup {
  domain: string;
  boxes: SenderCard[];
  enabled: boolean;
  sentToday: number;
  allowance: number;
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

      {groups.map((group) => (
        <Card key={group.domain} className="glass liftable" p="lg">
          <Group justify="space-between" align="flex-start" wrap="nowrap">
            <Stack gap="xs" style={{ flex: 1 }}>
              <Group gap="sm">
                <Title order={5}>{group.domain}</Title>
                <Badge variant="light" color={group.enabled ? 'green' : 'gray'}>
                  {group.enabled ? 'отправляет' : 'выключен'}
                </Badge>
                {group.boxes.some((box) => !box.warmup_finished && box.enabled) && (
                  <Badge variant="light" color="blue">
                    разгон, день {Math.max(...group.boxes.map((box) => box.warmup_day))}
                  </Badge>
                )}
              </Group>

              <Text size="sm" c="dimmed">
                Ящиков {group.boxes.length}. Сегодня ушло {group.sentToday} из {group.allowance} —
                потолок дня, а не дневной кап: на разгоне он ниже.
              </Text>

              <Progress
                value={
                  group.allowance === 0
                    ? 0
                    : Math.min(100, (group.sentToday / group.allowance) * 100)
                }
                color={group.enabled ? 'lagoon' : 'gray'}
                radius="xl"
                size="sm"
                maw={420}
              />

              {group.boxes.map((box) => (
                <Group key={box.id} gap="xs" className="glassSlot" p="xs">
                  <Text size="sm">{box.email}</Text>
                  <Text size="xs" c="dimmed">
                    {box.sent_today} / {box.warmup_allowance} писем сегодня
                  </Text>
                  {box.pause_reason !== null && (
                    <Badge variant="light" color="yellow">
                      {box.pause_reason}
                    </Badge>
                  )}
                </Group>
              ))}
            </Stack>

            <Tooltip
              label={
                group.enabled
                  ? 'Домен перестанет получать новые письма'
                  : 'Разгон начнётся с начала: полный кап сразу — это добить пошатнувшуюся репутацию'
              }
              multiline
              w={260}
              withArrow
            >
              <Button
                className="press"
                variant={group.enabled ? 'default' : 'gradient'}
                gradient={{ from: 'lagoon.5', to: 'lagoon.7', deg: 135 }}
                loading={
                  switchDomain.isPending && switchDomain.variables?.group.domain === group.domain
                }
                onClick={() => switchDomain.mutate({ group, on: !group.enabled })}
              >
                {group.enabled ? 'Выключить' : 'Включить с начала разгона'}
              </Button>
            </Tooltip>
          </Group>
        </Card>
      ))}
    </Stack>
  );
}
