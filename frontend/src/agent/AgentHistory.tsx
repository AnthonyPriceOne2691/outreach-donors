/**
 * Версии настроек агента одного этапа: что стояло, кто поставил и когда.
 *
 * Список, а не таблица: у версии одна строка смысла — включён ли, предел
 * цены, сколько доводов и тем, — и колонки чисел без сравнения по ним
 * читались бы отчётом, которого здесь нет.
 *
 * **Свёрнут в «Версии · N»** (аудит экранов 09.10.2026; карточка —
 * `components/FoldCard`): действующая версия названа строкой наверху экрана,
 * а прежние нужны, когда разбираются, что стояло раньше, — не при каждом
 * заходе. Версий нет — карточки нет: что этап не настроен, сказано строкой
 * наверху, второй абзац о том же был лишним.
 */

import { Badge, Group, Stack, Text } from '@mantine/core';
import { useState } from 'react';

import type { AgentSettingsBody, AgentStageView } from '../api/agent';
import { FoldCard } from '../components/FoldCard';
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
  if (view.history.length === 0) return null;
  return (
    <FoldCard
      label={`Версии · ${view.history.length}`}
      open={open}
      onToggle={() => setOpen((was) => !was)}
    >
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
    </FoldCard>
  );
}
