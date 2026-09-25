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

import type { SenderCard as Mailbox } from '../api/types';
import { Meter } from '../components/Meter';

export interface DomainGroup {
  domain: string;
  boxes: Mailbox[];
  enabled: boolean;
  sentToday: number;
  allowance: number;
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
            <Badge variant="light" color={group.enabled ? 'green' : 'gray'}>
              {group.enabled ? 'отправляет' : 'выключен'}
            </Badge>
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
              {group.boxes.length} ящ. · {group.sentToday} из {group.allowance} сегодня
            </Text>
          </Group>

          {!group.enabled && (
            <Text size="xs" c="dimmed">
              Включённый заново домен начинает разгон с начала.
            </Text>
          )}

          {/* Общая полоса «потрачено из лимита», как у расхода на главной:
              серая дорожка Mantine на стекле читалась чужой деталью (аудит
              25.09.2026). Янтарь с 80 % — дневной потолок разгона близко. */}
          <Box maw={320}>
            <Meter
              spent={group.sentToday}
              cap={group.allowance}
              label={`Отправлено сегодня ${group.sentToday} из ${group.allowance}`}
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
                  {box.sent_today} / {box.warmup_allowance} писем сегодня
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
