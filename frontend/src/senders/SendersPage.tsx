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
 *
 * **Домены — строками одной таблицы, а не карточками** (разведка 10.11.2026): карточки доменов по
 * 112 px, блок ящиков под ними и раскрытие карточки показывали одни и те же ящики и «N из M» трижды,
 * и страница уходила за три тысячи пикселей. Строка домена (`DomainRow`) — состояние, первые письма
 * сегодня с полосой и кнопка, ящики — раскрытием строки, блок «Ящики и разгон» — свёрнут с итогом.
 */

import { Alert, Card, Loader, Stack, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { disableSender, enableSender } from '../api/outreach';
import { SENDERS_TITLES, STAGE_TITLES, listSendersByStage } from '../api/senders';
import type { DirectionLimit, DomainLimit, SendersByStage, StagedSender } from '../api/senders';
import { stageShown } from '../api/stageRights';
import type { Can } from '../api/stageRights';
import type { Part, Stage } from '../api/stages';
import { partOf } from '../api/stages';
import { useSession } from '../auth/AuthProvider';
import type { Column } from '../components/ColumnsHead';
import { DataTable } from '../components/DataTable';
import { Cards } from '../components/MailingBlock';
import { formatNumber, plural } from '../format';
import { DomainRow, domainShut, plainState } from './DomainRow';
import type { DomainGroup } from './DomainRow';
import { StageBoxes } from './StageBoxes';
import { notify } from '../notices';
import { PageHead } from '../components/PageHead';

const SENDERS_QUERY_KEY = ['senders'] as const;
const STAGES = Object.keys(STAGE_TITLES) as Stage[];

/** Колонки таблицы доменов: имя берёт остаток (не уже 14rem — уже таблица уезжает в прокрутку), у
 *  кнопки — своя безымянная колонка справа. Счёт — первые письма, как у капа и разгона: так и
 *  сказано в заголовке, а не в каждой строке. */
const NAME_REM = 14;
const STATE_REM = 17;
const SENT_REM = 13;
/** По «Включить заново» с подписью под ней. */
const SWITCH_REM = 11;

/** Почему писать нечем: все выключены — или включённые закрыты строкой домена. */
const ALL_OFF =
  'Все домены выключены. Новые письма не уйдут, пока хотя бы один не включат — и он начнёт с начала разгона.';
const ALL_SHUT =
  'Включённые домены закрыты: на паузе, на выдержке или записаны за другим направлением — что именно, сказано в строке домена. Новые письма не уйдут, пока хотя бы один не откроется.';
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

/** Лимит направления строкой над таблицей доменов — с кромки текста таблицы; своего лимита у
 *  направления нет — строки нет. Настройка лимита не прочиталась — строка называет её, по смыслу как
 *  отказ отправки (`outreach/limits.py`): лимит тогда ноль, и «0 из 0» читался бы как выбранный
 *  лимит, а не как опечатка в .env. */
function DirectionLine({ direction }: { direction: DirectionLimit | undefined }) {
  if (direction?.unread != null) {
    return (
      <Text size="sm" px="md">
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
    <Text size="sm" px="md">
      {`Лимит направления: ${formatNumber(direction.sent_today)} из ${formatNumber(direction.daily_limit)} первых писем сегодня`}
    </Text>
  );
}

interface SectionProps {
  groups: DomainGroup[];
  direction: DirectionLimit | undefined;
  /** Имя таблицы для читалки экрана — какие это домены. */
  label: string;
  mayEdit: boolean;
  stageInTitle: boolean;
  /** Колонка состояния — одна на все разделы экрана, чтобы колонки разделов стояли друг под другом. */
  withState: boolean;
  /** Какой домен сейчас переключается — у его кнопки крутилка. */
  busy: string | null;
  onSwitch: (group: DomainGroup, on: boolean) => void;
}

/** Колонки таблицы доменов; колонки состояния нет, когда она одинакова во всех строках. */
function domainColumns(withState: boolean): Column[] {
  const state: Column[] = withState ? [{ title: 'Состояние', width: `${STATE_REM}rem` }] : [];
  return [{ title: 'Домен' }, ...state, { title: 'Первых писем сегодня', width: `${SENT_REM}rem` }];
}

/** Колонка, одинаковая во всех строках, не рисуется: у всех одно «отправляет» — колонки состояния
 *  нет, это уже сказала строка «Отправлять могут N из N» над таблицей (`plainState`). */
function stateShown(groups: DomainGroup[], stageInTitle: boolean): boolean {
  return !(groups.length > 1 && groups.every((one) => plainState(one, stageInTitle)));
}

/** Домены этапа одной таблицей: строка лимита направления над ней, строка домена — `DomainRow`. */
function DomainTable({
  groups,
  direction,
  label,
  mayEdit,
  stageInTitle,
  withState,
  busy,
  onSwitch,
}: SectionProps) {
  const columns = domainColumns(withState);
  const span = columns.length + (mayEdit ? 1 : 0);
  const rem = NAME_REM + (withState ? STATE_REM : 0) + SENT_REM + (mayEdit ? SWITCH_REM : 0);
  return (
    <Card className="glassPanel" p="md">
      <Stack gap="sm">
        <DirectionLine direction={direction} />
        <Cards>
          <DataTable
            columns={columns}
            minWidth={rem * 16}
            tail={mayEdit ? `${SWITCH_REM}rem` : null}
            className="domainTable"
            label={label}
            cardRow
          >
            {groups.map((group) => (
              <DomainRow
                key={group.domain}
                group={group}
                busy={busy === group.domain}
                onSwitch={(on) => onSwitch(group, on)}
                mayEdit={mayEdit}
                stageInTitle={stageInTitle}
                withState={withState}
                span={span}
              />
            ))}
          </DataTable>
        </Cards>
      </Stack>
    </Card>
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

interface Section {
  stage: Stage;
  groups: DomainGroup[];
  direction: DirectionLimit | undefined;
}

/** Разделы по этапу — этапы, у которых есть домены. Заголовком раздел встаёт, только когда этапов
 *  больше одного: у одних доноров экран прежний. */
function sectionsOf(groups: DomainGroup[], directions: DirectionLimit[] | undefined): Section[] {
  return STAGES.map((stage) => ({
    stage,
    groups: groups.filter((group) => group.stage === stage),
    direction: directions?.find((direction) => direction.stage === stage),
  })).filter((section) => section.groups.length > 0);
}

/** Что на экране: домены, разделы по этапу и какие колонки у их таблиц. */
interface Layout {
  groups: DomainGroup[];
  sections: Section[];
  /** Этап назван заголовком — экрана части или раздела: пометки у каждой строки нет. */
  stageInTitle: boolean;
  /** Колонка состояния — одна на все разделы: колонки разделов стоят друг под другом. */
  withState: boolean;
}

function layoutOf(data: SendersByStage | undefined, can: Can, part: Part | null): Layout {
  const groups = groupByDomain(shownBoxes(data?.senders ?? [], can, part), data?.domains ?? []);
  const sections = sectionsOf(groups, data?.directions);
  const stageInTitle = part !== null || sections.length > 1;
  return { groups, sections, stageInTitle, withState: stateShown(groups, stageInTitle) };
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

  const { groups, sections, stageInTitle, withState } = layoutOf(data, can, part);

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap="sm">
          <PageHead
            title={part === null ? 'Домены рассылки' : SENDERS_TITLES[part]}
            hint="Репутация живёт у домена, поэтому включают и выключают домен целиком. Выключенный не получает новых писем, но начатые цепочки не рвутся: письмо, отправленное вчера, ждёт ответа."
          />
          <Standing groups={groups} part={part} />
          {can('senders') ? null : (
            <Text size="sm" c="dimmed">
              Только просмотр: включать и выключать домены может администратор или учётка с правом
              «Домены рассылки».
            </Text>
          )}
        </Stack>
      </Card>

      {sections.map((section) => (
        <Stack gap="sm" key={section.stage}>
          {sections.length > 1 && <Title order={4}>{STAGE_TITLES[section.stage]}</Title>}
          <DomainTable
            groups={section.groups}
            direction={section.direction}
            label={SENDERS_TITLES[section.stage]}
            mayEdit={can('senders')}
            stageInTitle={stageInTitle}
            withState={withState}
            busy={switchDomain.isPending ? (switchDomain.variables?.group.domain ?? null) : null}
            onSwitch={(group, on) => switchDomain.mutate({ group, on })}
          />
        </Stack>
      ))}

      {part !== null && <StageBoxes stage={part} />}
    </Stack>
  );
}
