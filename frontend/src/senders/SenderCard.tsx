/**
 * Домен рассылки: короткая строка и раскрытие подробностей.
 *
 * **Карточка не подпрыгивает под курсором.** Поднимающийся блок уместен
 * там, где по нему щёлкают целиком; здесь щёлкают по кнопке, а прыжок
 * под курсором мешает в неё попасть — и это раздражает ровно в тот
 * момент, когда человек выключает домен из-за жалобы.
 *
 * **Подробности раскрываются, а не занимают место всегда.** Доменов
 * четыре сейчас и двадцать потом; двадцать развёрнутых карточек — это
 * экран, по которому надо скроллить, чтобы увидеть, что всё в порядке.
 * Свёрнутая карточка отвечает на главный вопрос («отправляет или нет,
 * сколько ушло сегодня»), развёрнутая — на все остальные.
 */

import { Badge, Box, Button, Card, Collapse, Group, Stack, Text, Tooltip } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { IconChevronDown } from '@tabler/icons-react';
import { useState } from 'react';

import { STAGE_TITLES } from '../api/senders';
import type { DomainLimit, StagedSender as Mailbox } from '../api/senders';
import type { Stage } from '../api/stages';
import { THREAD_STAGE_NOTES } from '../api/stages';
import { Meter } from '../components/Meter';
import { formatDate, formatNumber } from '../format';

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

interface Props {
  group: DomainGroup;
  busy: boolean;
  onSwitch: (on: boolean) => void;
}

export function SenderCard({ group, busy, onSwitch }: Props) {
  const [open, setOpen] = useState(false);
  const detailsId = `sender-details-${group.domain}`;
  const warmupDay = Math.max(...group.boxes.map((box) => box.warmup_day));
  const warming = group.enabled && group.boxes.some((box) => !box.warmup_finished);
  const paused = group.boxes.find((box) => box.pause_reason !== null)?.pause_reason ?? null;
  const state = stateOf(group);

  // На телефоне кнопка уходит под карточку: рядом с ней имя домена
  // рвалось на «mail-» и остаток.
  const narrow = useMediaQuery('(max-width: 36em)') ?? false;
  const toggle = (
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
        style={{ flexShrink: 0 }}
      >
        {group.enabled ? 'Выключить' : 'Включить заново'}
      </Button>
    </Tooltip>
  );

  /* Строка устроена в два яруса: сверху имя домена и кнопка, снизу
     значки и счётчик, и нижний ярус переносится. В один ярус на узком
     окне имя ужималось до «m…», значки — до «о…», а длинная кнопка
     наезжала на текст. */
  return (
    <Card className="glass" p="md">
      <Group gap="sm" wrap="nowrap" align="flex-start">
        {/* `aria-expanded` и `aria-controls` — не украшение: без них
            программа чтения с экрана видит кнопку без состояния,
            а тест не может отличить раскрытую карточку от свёрнутой
            (в jsdom анимация высоты не проигрывается). */}
        <Button
          variant="subtle"
          size="compact-sm"
          px={6}
          mt={2}
          aria-label={open ? `Свернуть ${group.domain}` : `Подробности ${group.domain}`}
          aria-expanded={open}
          aria-controls={detailsId}
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

        <Stack gap={6} style={{ flex: 1, minWidth: 0 }}>
          <Group justify="space-between" wrap="nowrap" gap="sm">
            <Text fw={600} style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
              {group.domain}
            </Text>
            {!narrow && toggle}
          </Group>

          <Group gap="xs" wrap="wrap" align="center">
            {/* Включённый домен, закрытый строкой домена, не пишет: фильтр отправки
                отсеивает его ящики — зелёное «отправляет» здесь врало бы. */}
            <Badge variant="light" color={state.color} style={{ maxWidth: '100%' }}>
              {state.label}
            </Badge>
            {THREAD_STAGE_NOTES[group.stage] !== '' && (
              <Badge variant="outline" color="gray">
                {THREAD_STAGE_NOTES[group.stage]}
              </Badge>
            )}
            {warming && (
              <Badge variant="light" color="lagoon">
                разгон, день {warmupDay}
              </Badge>
            )}
            {/* Причина парковки видна в свёрнутом виде: именно по ней
                решают, включать домен обратно или разбираться дальше. */}
            {!group.enabled && paused !== null && (
              <Badge variant="light" color="yellow" style={{ maxWidth: '100%' }}>
                {paused}
              </Badge>
            )}
            <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
              {group.boxes.length} ящ. · {group.sentToday} из {group.allowance} первых писем сегодня
            </Text>
          </Group>

          {!group.enabled && (
            <Text size="xs" c="dimmed">
              Включённый заново домен начинает разгон с начала.
            </Text>
          )}
          {group.limit !== undefined && (
            <Text size="xs" c="dimmed">
              {limitLine(group.limit, group.stage)}
            </Text>
          )}

          {/* Общая полоса «потрачено из лимита», как у расхода на главной:
              серая дорожка Mantine на стекле читалась чужой деталью (аудит
              25.09.2026). Янтарь с 80 % — дневной потолок разгона близко. */}
          <Box maw={320}>
            <Meter
              spent={group.sentToday}
              cap={group.allowance}
              label={`Первых писем сегодня: ${group.sentToday} из ${group.allowance}`}
            />
          </Box>

          {narrow && <Group justify="flex-end">{toggle}</Group>}
        </Stack>
      </Group>

      <Collapse id={detailsId} in={open} transitionDuration={220} transitionTimingFunction="ease">
        <Stack gap={6} mt="sm" pt="sm" className="hairline">
          {group.boxes.map((box) => (
            <Group key={box.id} justify="space-between" className="glassSlot" p="xs" wrap="wrap">
              <Text size="sm" style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
                {box.email}
              </Text>
              <Group gap="xs" wrap="wrap">
                <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
                  {box.sent_today} / {box.warmup_allowance} первых писем сегодня
                </Text>
                {!box.warmup_finished && (
                  <Badge variant="light" color="lagoon" size="sm">
                    день {box.warmup_day} из разгона
                  </Badge>
                )}
              </Group>
            </Group>
          ))}
        </Stack>
      </Collapse>
    </Card>
  );
}
