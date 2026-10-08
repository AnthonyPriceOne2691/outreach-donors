/**
 * Домены рассылки: кто сейчас может отправлять и на каком разгоне.
 *
 * Экран сгруппирован по доменам, а не по ящикам: репутация живёт
 * у домена, а ящики на нём — это просто пропускная способность.
 * Человек решает «включить домен», а не «включить третий ящик».
 *
 * **Включённый заново домен начинает разгон с начала** — об этом
 * сказано прямо в карточке, а не в документации: домен выключают обычно
 * потому, что с ним что-то не так, и вернуть его сразу на полный кап
 * значит добить репутацию, которая и так пошатнулась.
 */

import { Alert, Card, Loader, Stack, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { disableSender, enableSender } from '../api/outreach';
import { STAGE_TITLES, listSendersByStage } from '../api/senders';
import type { DirectionLimit, DomainLimit, StagedSender } from '../api/senders';
import type { Stage } from '../api/stages';
import { formatNumber, plural } from '../format';
import { SenderCard as DomainCard, domainShut } from './SenderCard';
import type { DomainGroup } from './SenderCard';

const SENDERS_QUERY_KEY = ['senders'] as const;
const STAGES = Object.keys(STAGE_TITLES) as Stage[];

/** Почему писать нечем: все выключены — или включённые закрыты строкой домена. */
const ALL_OFF =
  'Все домены выключены. Новые письма не уйдут, пока хотя бы один не включат — и он начнёт с начала разгона.';
const ALL_SHUT =
  'Включённые домены закрыты: на паузе, на выдержке или записаны за другим направлением — что именно, сказано на карточке домена. Новые письма не уйдут, пока хотя бы один не откроется.';

function groupByDomain(senders: StagedSender[], limits: DomainLimit[]): DomainGroup[] {
  const groups = new Map<string, StagedSender[]>();
  for (const sender of senders) {
    groups.set(sender.domain, [...(groups.get(sender.domain) ?? []), sender]);
  }
  const now = new Date();
  return [...groups.entries()].map(([domain, boxes]) => {
    const stage = boxes[0]?.stage ?? 'donors';
    const limit = limits.find((row) => row.domain === domain);
    return {
      domain,
      stage,
      boxes,
      enabled: boxes.some((box) => box.enabled),
      shut: domainShut(limit, stage, now),
      sentToday: boxes.reduce((sum, box) => sum + box.sent_today, 0),
      allowance: boxes.reduce((sum, box) => sum + box.warmup_allowance, 0),
      limit,
    };
  });
}

/** Лимит направления словами; `null` — своего лимита у направления нет. */
function directionLine(direction: DirectionLimit | undefined): string | null {
  if (direction?.daily_limit == null) return null;
  return `Лимит направления: ${formatNumber(direction.sent_today)} из ${formatNumber(direction.daily_limit)} первых писем сегодня`;
}

export function SendersPage() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: SENDERS_QUERY_KEY,
    queryFn: listSendersByStage,
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

  const groups = groupByDomain(data?.senders ?? [], data?.domains ?? []);
  // Пишет домен, у которого включён ящик и который строка домена не закрыла: тот же
  // отбор, что у фильтра отправки, — иначе «могут» считал бы и тех, кого фильтр отсеет.
  const writing = groups.filter((group) => group.enabled && group.shut === null).length;
  // Разделы по этапу — только когда этапов больше одного: у одних доноров экран прежний.
  const sections = STAGES.map((stage) => ({
    stage,
    groups: groups.filter((group) => group.stage === stage),
    limit: directionLine(data?.directions?.find((direction) => direction.stage === stage)),
  })).filter((section) => section.groups.length > 0);

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
          {writing === 0 ? (
            <Alert color="yellow" title="Отправлять нечем">
              {groups.some((group) => group.enabled) ? ALL_SHUT : ALL_OFF}
            </Alert>
          ) : (
            <Text size="sm">
              Отправлять могут <b>{writing}</b> из {groups.length}{' '}
              {plural(groups.length, 'домена', 'доменов', 'доменов')}.
            </Text>
          )}
        </Stack>
      </Card>

      {sections.map((section) => (
        <Stack gap="sm" key={section.stage}>
          {sections.length > 1 && <Title order={4}>{STAGE_TITLES[section.stage]}</Title>}
          {section.limit !== null && <Text size="sm">{section.limit}</Text>}
          {section.groups.map((group) => (
            <DomainCard
              key={group.domain}
              group={group}
              busy={switchDomain.isPending && switchDomain.variables?.group.domain === group.domain}
              onSwitch={(on) => switchDomain.mutate({ group, on })}
            />
          ))}
        </Stack>
      ))}
    </Stack>
  );
}
