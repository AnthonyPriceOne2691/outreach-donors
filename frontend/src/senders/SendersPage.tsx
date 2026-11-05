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
 *
 * **Ящики продаж — только с правом «Продажи»** (решение Anthony 10.10.2026, П2б):
 * сервер без права их не отдаёт и переключить не даст, а экран не показывает их и сам —
 * ответ в кэше мог прийти до того, как право сняли.
 *
 * **У каждого этапа — своя часть экрана** (`?stage=…`): пункт «Домены рассылки» в группах меню
 * «Доноры», «Рекламодатели» и «Продажи» (просьба Anthony 05.11.2026, продажи — с 11.10.2026):
 * только домены этапа, его лимит и ящики с разгоном (`StageBoxes`). Без части (`/senders`, пункта
 * меню нет) — все этапы, как было.
 */

import { Alert, Card, Loader, Stack, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { disableSender, enableSender } from '../api/outreach';
import { SENDERS_TITLES, STAGE_TITLES, listSendersByStage } from '../api/senders';
import type { DirectionLimit, DomainLimit, StagedSender } from '../api/senders';
import { stageShown } from '../api/stageRights';
import type { Can } from '../api/stageRights';
import type { Part, Stage } from '../api/stages';
import { partOf } from '../api/stages';
import { useSession } from '../auth/AuthProvider';
import { formatNumber, plural } from '../format';
import { SenderCard as DomainCard, domainShut } from './SenderCard';
import type { DomainGroup } from './SenderCard';
import { StageBoxes } from './StageBoxes';
import { notify } from '../notices';
import { PageHead } from '../components/PageHead';

const SENDERS_QUERY_KEY = ['senders'] as const;
const STAGES = Object.keys(STAGE_TITLES) as Stage[];

/** Почему писать нечем: все выключены — или включённые закрыты строкой домена. */
const ALL_OFF =
  'Все домены выключены. Новые письма не уйдут, пока хотя бы один не включат — и он начнёт с начала разгона.';
const ALL_SHUT =
  'Включённые домены закрыты: на паузе, на выдержке или записаны за другим направлением — что именно, сказано на карточке домена. Новые письма не уйдут, пока хотя бы один не откроется.';
/** Части этапа без единого домена: это не «все выключены», а ещё не заведены. */
const NO_DOMAINS: Record<Part, string> = {
  donors: 'Доменов доноров пока нет: письма донорам не уйдут.',
  advertisers: 'Доменов рекламодателей пока нет: письма рекламодателям не уйдут.',
  sales: 'Доменов продаж пока нет: ящики продаж заводятся, когда подключают почту продаж.',
};

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

/** Лимит направления строкой; своего лимита у направления нет — строки нет. Настройка лимита
 *  не прочиталась — строка называет её, по смыслу как отказ отправки (`outreach/limits.py`):
 *  лимит тогда ноль, и «0 из 0» читался бы как выбранный лимит, а не как опечатка в .env. */
function DirectionLine({ direction }: { direction: DirectionLimit | undefined }) {
  if (direction?.unread != null) {
    return (
      <Text size="sm">
        Лимит направления: настройка{' '}
        <Text span size="xs" ff="monospace">
          {direction.unread}
        </Text>{' '}
        не прочитана — первых писем ноль, пока её не поправят в .env
      </Text>
    );
  }
  if (direction?.daily_limit == null) return null;
  return (
    <Text size="sm">
      {`Лимит направления: ${formatNumber(direction.sent_today)} из ${formatNumber(direction.daily_limit)} первых писем сегодня`}
    </Text>
  );
}

/** Сколько доменов может писать — или почему ни один: все выключены, закрыты строкой
 *  домена или, в части этапа, его доменов ещё нет. */
function Standing({ groups, part }: { groups: DomainGroup[]; part: Part | null }) {
  // Пишет домен, у которого включён ящик и который строка домена не закрыла: тот же
  // отбор, что у фильтра отправки, — иначе «могут» считал бы и тех, кого фильтр отсеет.
  const writing = groups.filter((group) => group.enabled && group.shut === null).length;
  if (part !== null && groups.length === 0) return <Text size="sm">{NO_DOMAINS[part]}</Text>;
  if (writing === 0) {
    return (
      <Alert color="yellow" title="Отправлять нечем">
        {groups.some((group) => group.enabled) ? ALL_SHUT : ALL_OFF}
      </Alert>
    );
  }
  return (
    <Text size="sm">
      Отправлять могут <b>{writing}</b> из {groups.length}{' '}
      {plural(groups.length, 'домена', 'доменов', 'доменов')}.
    </Text>
  );
}

/** Ящики на экране: без права этапа — без его ящиков, в части этапа — только они. */
function shownBoxes(boxes: StagedSender[], can: Can, part: Part | null): StagedSender[] {
  return boxes.filter((box) => stageShown(box.stage, can) && (part === null || box.stage === part));
}

/** Часть этапа из адреса — только с его правом: без него ящиков этапа на экране нет и так (П2б). */
function shownPart(params: URLSearchParams, can: Can): Part | null {
  const named = partOf(params);
  return named !== null && stageShown(named, can) ? named : null;
}

export function SendersPage() {
  const { can } = useSession();
  const [params] = useSearchParams();
  const part = shownPart(params, can);
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
      notify({
        message: on
          ? `${group.domain} включён — разгон начался заново`
          : `${group.domain} выключен, начатые цепочки не рвутся`,
        color: on ? 'green' : 'yellow',
      });
    },
    onError: (failure) =>
      notify({ title: 'Не переключили', message: refusalOf(failure), color: 'red' }),
  });

  if (isLoading) return <Loader aria-label="Загружаем домены рассылки" m="md" />;
  if (error) {
    return (
      <Alert color="red" title="Домены не загрузились" m="md">
        {refusalOf(error)}
      </Alert>
    );
  }

  const boxes = shownBoxes(data?.senders ?? [], can, part);
  const groups = groupByDomain(boxes, data?.domains ?? []);
  // Разделы по этапу — только когда этапов больше одного: у одних доноров экран прежний.
  const sections = STAGES.map((stage) => ({
    stage,
    groups: groups.filter((group) => group.stage === stage),
    direction: data?.directions?.find((direction) => direction.stage === stage),
  })).filter((section) => section.groups.length > 0);

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="sm">
          <PageHead
            title={part === null ? 'Домены рассылки' : SENDERS_TITLES[part]}
            hint="Репутация живёт у домена, поэтому включают и выключают домен целиком. Выключенный не получает новых писем, но начатые цепочки не рвутся: письмо, отправленное вчера, ждёт ответа."
          />
          <Standing groups={groups} part={part} />
        </Stack>
      </Card>

      {sections.map((section) => (
        <Stack gap="sm" key={section.stage}>
          {sections.length > 1 && <Title order={4}>{STAGE_TITLES[section.stage]}</Title>}
          <DirectionLine direction={section.direction} />
          {section.groups.map((group) => (
            <DomainCard
              key={group.domain}
              group={group}
              busy={switchDomain.isPending && switchDomain.variables?.group.domain === group.domain}
              onSwitch={(on) => switchDomain.mutate({ group, on })}
              stageInTitle={part !== null}
            />
          ))}
        </Stack>
      ))}

      {part !== null && <StageBoxes stage={part} />}
    </Stack>
  );
}
