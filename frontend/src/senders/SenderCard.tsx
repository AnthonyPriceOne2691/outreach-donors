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

import {
  Badge,
  Button,
  Card,
  Collapse,
  Group,
  Progress,
  Stack,
  Text,
  Tooltip,
} from '@mantine/core';
import { IconChevronDown } from '@tabler/icons-react';
import { useState } from 'react';

import type { SenderCard as Box } from '../api/types';

export interface DomainGroup {
  domain: string;
  boxes: Box[];
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

  return (
    <Card className="glass" p="md">
      <Group justify="space-between" wrap="nowrap" gap="md">
        <Group gap="sm" wrap="nowrap" style={{ flex: 1, minWidth: 0 }}>
          {/* `aria-expanded` и `aria-controls` — не украшение: без них
              программа чтения с экрана видит кнопку без состояния,
              а тест не может отличить раскрытую карточку от свёрнутой
              (в jsdom анимация высоты не проигрывается). */}
          <Button
            variant="subtle"
            size="compact-sm"
            px={6}
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

          <Stack gap={2} style={{ flex: 1, minWidth: 0 }}>
            <Group gap="xs" wrap="nowrap">
              <Text fw={600} truncate>
                {group.domain}
              </Text>
              <Badge variant="light" color={group.enabled ? 'green' : 'gray'}>
                {group.enabled ? 'отправляет' : 'выключен'}
              </Badge>
              {warming && (
                <Badge variant="light" color="blue">
                  разгон, день {warmupDay}
                </Badge>
              )}
            </Group>

            <Group gap="sm" wrap="nowrap">
              <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
                {group.boxes.length} ящ. · {group.sentToday} из {group.allowance} сегодня
              </Text>
              {/* Причина парковки видна в свёрнутом виде: именно по ней
                  решают, включать домен обратно или разбираться дальше. */}
              {!group.enabled && paused !== null && (
                <Badge variant="light" color="yellow" size="sm">
                  {paused}
                </Badge>
              )}
              <Progress
                value={
                  group.allowance === 0
                    ? 0
                    : Math.min(100, (group.sentToday / group.allowance) * 100)
                }
                color={group.enabled ? 'lagoon' : 'gray'}
                radius="xl"
                size="xs"
                style={{ flex: 1, maxWidth: 220 }}
              />
            </Group>
          </Stack>
        </Group>

        <Tooltip
          label={
            group.enabled
              ? 'Домен перестанет получать новые письма, начатые цепочки не рвутся'
              : 'Разгон начнётся с начала: полный кап сразу — это добить пошатнувшуюся репутацию'
          }
          multiline
          w={260}
          withArrow
        >
          <Button
            className="press"
            size="compact-sm"
            variant={group.enabled ? 'default' : 'gradient'}
            loading={busy}
            onClick={() => onSwitch(!group.enabled)}
          >
            {group.enabled ? 'Выключить' : 'Включить с начала разгона'}
          </Button>
        </Tooltip>
      </Group>

      <Collapse id={detailsId} in={open} transitionDuration={220} transitionTimingFunction="ease">
        <Stack gap={6} mt="sm" pt="sm" className="hairline">
          {group.boxes.map((box) => (
            <Group key={box.id} justify="space-between" className="glassSlot" p="xs" wrap="nowrap">
              <Text size="sm" truncate>
                {box.email}
              </Text>
              <Group gap="xs" wrap="nowrap">
                <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap' }}>
                  {box.sent_today} / {box.warmup_allowance} писем сегодня
                </Text>
                {!box.warmup_finished && (
                  <Badge variant="light" color="blue" size="sm">
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
