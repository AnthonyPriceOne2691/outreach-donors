/**
 * Версии настроек агента одного этапа: что стояло, кто поставил и когда.
 *
 * Список, а не таблица: у версии одна строка смысла — включён ли, предел
 * цены, сколько доводов и тем, — и колонки чисел без сравнения по ним
 * читались бы отчётом, которого здесь нет.
 *
 * **Свёрнут в «Версии · N»** (аудит экранов 09.10.2026): действующая версия
 * названа строкой наверху экрана, а прежние нужны, когда разбираются, что
 * стояло раньше, — не при каждом заходе. Версий нет — карточки нет: что этап
 * не настроен, сказано строкой наверху, второй абзац о том же был лишним.
 */

import { Badge, Button, Card, Collapse, Group, Stack, Text } from '@mantine/core';
import { IconChevronDown } from '@tabler/icons-react';
import { useId, useState } from 'react';

import type { AgentSettingsBody, AgentStageView } from '../api/agent';
import { formatDateTime, formatUsd } from '../format';

interface HistoryProps {
  view: AgentStageView;
  /** Предел у этапа словами: «не дороже» или «не дешевле». */
  limitWord: string;
}

function summaryOf(settings: AgentSettingsBody, limitWord: string): string {
  const limit =
    settings.price_limit_usd === null
      ? 'предела цены нет'
      : `${limitWord} ${formatUsd(settings.price_limit_usd)}`;
  return [
    settings.enabled ? 'пишет черновики' : 'выключен',
    limit,
    `доводов: ${settings.points.length}`,
    `тем человеку: ${settings.stop_topics.length}`,
  ].join(' · ');
}

export function AgentHistory({ view, limitWord }: HistoryProps) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  if (view.history.length === 0) return null;
  return (
    <Card className="glass" px="xl" py="md">
      <Group>
        <Button
          variant="subtle"
          size="compact-md"
          px={6}
          leftSection={
            <IconChevronDown
              size={16}
              style={{
                transform: open ? 'rotate(180deg)' : 'none',
                transition: 'transform 200ms cubic-bezier(0.32, 0.72, 0, 1)',
              }}
            />
          }
          aria-expanded={open}
          aria-controls={listId}
          onClick={() => setOpen((was) => !was)}
        >
          Версии · {view.history.length}
        </Button>
      </Group>
      <Collapse id={listId} in={open} transitionDuration={220} transitionTimingFunction="ease">
        <Stack gap="xs" mt="sm">
          {view.history.map(({ version, settings, created_by: author, created_at: at }) => (
            <Group key={version} justify="space-between" gap="xs" className="agentVersion">
              <Group gap="xs">
                <Text fw={500}>№{version}</Text>
                {version === view.current?.version && (
                  <Badge variant="light" color="green">
                    действует
                  </Badge>
                )}
                <Text size="sm">{summaryOf(settings, limitWord)}</Text>
              </Group>
              <Text size="sm" c="dimmed" className="agentVersionWho">
                {author === null ? formatDateTime(at) : `${author} · ${formatDateTime(at)}`}
              </Text>
            </Group>
          ))}
        </Stack>
      </Collapse>
    </Card>
  );
}
