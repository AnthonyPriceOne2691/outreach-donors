/**
 * Домен рассылки строкой таблицы доменов: имя, состояние, первые письма сегодня с полосой и кнопка;
 * ящики домена — раскрытием под строкой.
 *
 * **Строка, а не карточка** (разведка 10.11.2026): карточка домена в 112 px держала одну строку
 * текста в треть ширины, а карточек стоял десяток с лишним — до блока ящиков, и страница уходила за
 * три тысячи пикселей. Строка отвечает на тот же главный вопрос — отправляет ли домен и сколько ушло
 * сегодня — в одну высоту.
 *
 * **Строка не подпрыгивает под курсором.** Щёлкают по кнопке, а прыжок под курсором мешает в неё
 * попасть — и это раздражает ровно в тот момент, когда человек выключает домен из-за жалобы.
 *
 * **Ящики раскрываются, а не занимают место всегда.** Счёт ящика — в раскрытии, только когда ящиков
 * на домене несколько: у единственного он тот же, что у строки, и одно число стояло бы дважды.
 *
 * **Что закрыло домен — значком, коротко** (пауза, выдержка, чужое направление), строка лимита
 * домена целиком — по нажатию «i» рядом. Причина парковки выключенного домена — на виду: по ней
 * решают, включать его обратно.
 */

import { Badge, Box, Button, Group, Stack, Table, Text, Tooltip } from '@mantine/core';
import { IconChevronDown } from '@tabler/icons-react';
import { useState } from 'react';

import { STAGE_TITLES } from '../api/senders';
import type { DomainLimit, StagedSender as Mailbox } from '../api/senders';
import type { Stage } from '../api/stages';
import { THREAD_STAGE_NOTES } from '../api/stages';
import { InfoHint } from '../components/InfoHint';
import { Meter } from '../components/Meter';
import { Seams } from '../components/Seams';
import { WholeRow } from '../components/WholeRow';
import { formatDate, formatNumber, plural } from '../format';

export interface DomainGroup {
  domain: string;
  /** Направление домена: у этапов домены отправки свои. */
  stage: Stage;
  boxes: Mailbox[];
  enabled: boolean;
  /** Чем строка домена закрывает его для направления (`domainShut`); `null` — открыт. */
  shut: string | null;
  sentToday: number;
  allowance: number;
  /** Лимит домена целиком (`sending_domains`); нет строки — у домена лимита нет. */
  limit: DomainLimit | undefined;
}

/**
 * Чем строка домена закрывает его для направления ящиков — правило фильтра отправки
 * (`outreach/limits.domain_shut`): чужое направление, пауза, выдержка. `null` — открыт:
 * строки нет или она его. Выбранный лимит дня не закрывает — завтра домен пишет снова.
 */
export function domainShut(
  limit: DomainLimit | undefined,
  stage: Stage,
  now: Date = new Date(),
): string | null {
  if (limit === undefined) return null;
  // Коротко: «домен записан за другим направлением» на 375 px обрезался; за каким —
  // называет строка лимита.
  if (limit.stage !== stage) return 'домен другого направления';
  if (limit.paused_at !== null) return 'домен на паузе';
  if (limit.young_until !== null && new Date(limit.young_until) > now) return 'домен на выдержке';
  return null;
}

/** Значок состояния: пишет — мята, закрыт строкой домена — янтарь, выключен — серый. */
function stateOf(group: DomainGroup): { label: string; color: string } {
  if (!group.enabled) return { label: 'выключен', color: 'gray' };
  if (group.shut !== null) return { label: group.shut, color: 'yellow' };
  return { label: 'отправляет', color: 'green' };
}

/** Лимит домена словами: счёт первых писем, чужое направление, выдержка, пауза. */
export function limitLine(limit: DomainLimit, stage: Stage): string {
  const parts = [
    `лимит домена: ${formatNumber(limit.sent_today)} из ${formatNumber(limit.daily_limit)} первых писем сегодня`,
  ];
  if (limit.stage !== stage) parts.push(`записан за направлением «${STAGE_TITLES[limit.stage]}»`);
  if (limit.young_until !== null && new Date(limit.young_until) > new Date()) {
    parts.push(`на выдержке до ${formatDate(limit.young_until)}`);
  }
  if (limit.paused_at !== null)
    parts.push(`домен на паузе: ${limit.pause_reason ?? 'без причины'}`);
  return parts.join(' · ');
}

/** Лимит домена под полосой: счёт домена — только когда он не тот же, что у строки (на домене
 *  пишут и ящики другого направления). */
function domainCap(limit: DomainLimit, sent: number): string {
  const cap = formatNumber(limit.daily_limit);
  return limit.sent_today === sent
    ? `лимит домена: ${cap}`
    : `лимит домена: ${formatNumber(limit.sent_today)} из ${cap}`;
}

/** Домен на разгоне: включён, и хоть один его ящик разгон не кончил. */
function warmingOf(group: DomainGroup): boolean {
  return group.enabled && group.boxes.some((box) => !box.warmup_finished);
}

/** Пометка этапа у строки — только на экране всех этапов, где этап не назван заголовком. */
function noteOf(group: DomainGroup, stageInTitle: boolean): string {
  return stageInTitle ? '' : THREAD_STAGE_NOTES[group.stage];
}

/** В колонке состояния у домена одно «отправляет»: ни разгона, ни пометки этапа, ни закрытия. Так у
 *  всех строк — колонки нет: то же говорит строка «Отправлять могут N из N» над таблицей. */
export function plainState(group: DomainGroup, stageInTitle: boolean): boolean {
  return (
    group.enabled && group.shut === null && !warmingOf(group) && noteOf(group, stageInTitle) === ''
  );
}

/** Ячейка состояния: у закрытого и выключенного домена значок и причина длиннее половины карточки
 *  телефона — там она во всю ширину (`phoneWide`). */
function stateCell(group: DomainGroup): string {
  return group.enabled && group.shut === null ? 'wrapCell' : 'wrapCell phoneWide';
}

/** Состояние домена значками; что закрыло — целиком в «i»; у выключенного — причина парковки. */
function DomainState({ group, stageInTitle }: { group: DomainGroup; stageInTitle: boolean }) {
  const state = stateOf(group);
  const note = noteOf(group, stageInTitle);
  const warming = warmingOf(group);
  const parked = group.enabled ? null : group.boxes.find((box) => box.pause_reason !== null);
  return (
    <Stack gap={4} align="center">
      <Group gap={6} justify="center">
        {/* Включённый домен, закрытый строкой домена, не пишет: фильтр отправки отсеивает его
            ящики — зелёное «отправляет» здесь врало бы. */}
        <Badge variant="light" color={state.color}>
          {state.label}
        </Badge>
        {group.shut !== null && group.limit !== undefined ? (
          <InfoHint name={`Чем закрыт ${group.domain}`} interactive width={300}>
            <Text size="sm">{limitLine(group.limit, group.stage)}</Text>
          </InfoHint>
        ) : null}
        {note === '' ? null : (
          <Badge variant="outline" color="gray">
            {note}
          </Badge>
        )}
        {warming ? (
          <Badge variant="light" color="lagoon">
            {`разгон, день\u00a0${Math.max(...group.boxes.map((box) => box.warmup_day))}`}
          </Badge>
        ) : null}
      </Group>
      {parked?.pause_reason ? <Text size="xs">{parked.pause_reason}</Text> : null}
    </Stack>
  );
}

/** Первые письма сегодня: число, общая полоса расхода (`Meter`) и лимит домена, если он есть. */
function SentToday({ group }: { group: DomainGroup }) {
  const said = `${formatNumber(group.sentToday)} из ${formatNumber(group.allowance)}`;
  return (
    <Stack gap={4} align="center">
      <Text size="sm">{said}</Text>
      {/* Общая полоса «потрачено из лимита», как у расхода на главной: серая дорожка Mantine на
          стекле читалась чужой деталью (аудит 25.09.2026). Янтарь с 80 % — потолок дня близко. */}
      <Box w="100%" maw={160}>
        <Meter
          spent={group.sentToday}
          cap={group.allowance}
          label={`Первых писем сегодня: ${said}`}
        />
      </Box>
      {group.limit === undefined ? null : (
        <Text size="xs" c="dimmed">
          {domainCap(group.limit, group.sentToday)}
        </Text>
      )}
    </Stack>
  );
}

/** Кнопка домена и последствие нажатия: включённый заново домен начинает разгон с начала. */
function DomainSwitch({ group, busy, onSwitch }: Pick<Props, 'group' | 'busy' | 'onSwitch'>) {
  return (
    <Stack gap={2} align="center">
      <Tooltip
        label={
          group.enabled
            ? 'Домен перестанет получать новые письма, начатые цепочки не рвутся'
            : 'Полный кап сразу добил бы пошатнувшуюся репутацию, поэтому разгон идёт с начала'
        }
        multiline
        w={260}
        withArrow
      >
        <Button
          className="press"
          size="xs"
          variant={group.enabled ? 'default' : 'gradient'}
          loading={busy}
          onClick={() => onSwitch(!group.enabled)}
        >
          {group.enabled ? 'Выключить' : 'Включить заново'}
        </Button>
      </Tooltip>
      {group.enabled ? null : (
        <Text size="xs" c="dimmed">
          начнёт разгон с начала
        </Text>
      )}
    </Stack>
  );
}

/** Ящики домена под строкой — с кромки имени домена (после шеврона); адрес — по швам; счёт и разгон
 *  ящика — только когда их несколько. */
function DomainBoxes({ group, id }: { group: DomainGroup; id: string }) {
  const many = group.boxes.length > 1;
  return (
    <Stack gap={4} id={id} ps={34}>
      {group.boxes.map((box) => (
        <Group key={box.id} gap="xs">
          <Text size="sm" className="cellName">
            <Seams text={box.email} address />
          </Text>
          {many ? (
            <Text size="xs" c="dimmed">
              {`${formatNumber(box.sent_today)} из ${formatNumber(box.warmup_allowance)} первых писем сегодня`}
            </Text>
          ) : null}
          {many && !box.warmup_finished ? (
            <Badge variant="light" color="lagoon" size="sm">
              {`разгон, день\u00a0${box.warmup_day}`}
            </Badge>
          ) : null}
        </Group>
      ))}
    </Stack>
  );
}

interface Props {
  group: DomainGroup;
  busy: boolean;
  onSwitch: (on: boolean) => void;
  /** Право включать и выключать; без него строка только для чтения, кнопки нет. */
  mayEdit: boolean;
  /** Этап назван заголовком — экрана одной части или раздела: пометка у каждой строки была бы
   *  шумом (пометка, общая для строк, — одной строкой группы). */
  stageInTitle?: boolean;
  /** Колонка состояния: у всех строк одно «отправляет» — её нет (`plainState`). */
  withState?: boolean;
  /** Колонок в таблице: ящики встают под строкой на всю её ширину. */
  span: number;
}

export function DomainRow({
  group,
  busy,
  onSwitch,
  mayEdit,
  stageInTitle = false,
  withState = true,
  span,
}: Props) {
  const [open, setOpen] = useState(false);
  const boxesId = `sender-boxes-${group.domain}`;
  const boxes = `${formatNumber(group.boxes.length)} ${plural(group.boxes.length, 'ящик', 'ящика', 'ящиков')}`;
  return (
    <>
      <Table.Tr>
        <Table.Td className="cellName">
          <Group gap={6} wrap="nowrap">
            {/* `aria-expanded` и `aria-controls` — не украшение: без них программа чтения с
                экрана видит кнопку без состояния, а тест не отличит раскрытое от свёрнутого.
                Имя говорит, что внутри и сколько. */}
            <Button
              variant="subtle"
              size="compact-sm"
              px={6}
              aria-label={
                open ? `Свернуть ящики ${group.domain}` : `Ящики ${group.domain} · ${boxes}`
              }
              aria-expanded={open}
              aria-controls={boxesId}
              onClick={() => setOpen((was) => !was)}
            >
              <IconChevronDown
                size={16}
                style={{
                  transform: open ? 'rotate(180deg)' : 'none',
                  transition: 'transform 200ms cubic-bezier(0.32, 0.72, 0, 1)',
                }}
              />
            </Button>
            <Text size="sm" fw={600} className="domainName">
              <Seams text={group.domain} />
            </Text>
          </Group>
        </Table.Td>
        {withState ? (
          <Table.Td className={stateCell(group)} data-label="Состояние">
            <DomainState group={group} stageInTitle={stageInTitle} />
          </Table.Td>
        ) : null}
        <Table.Td data-label="Первых писем сегодня">
          <SentToday group={group} />
        </Table.Td>
        {mayEdit ? (
          <Table.Td className="cellDecide">
            <DomainSwitch group={group} busy={busy} onSwitch={onSwitch} />
          </Table.Td>
        ) : null}
      </Table.Tr>
      {open ? (
        <WholeRow span={span}>
          <DomainBoxes group={group} id={boxesId} />
        </WholeRow>
      ) : null}
    </>
  );
}
