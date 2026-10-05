/**
 * Версии настроек агента одного этапа: что стояло, кто поставил и когда.
 *
 * Список, а не таблица: у версии одна строка смысла — включён ли, предел
 * цены, сколько доводов и тем, — и колонки чисел без сравнения по ним
 * читались бы отчётом, которого здесь нет.
 */

import { Badge, Card, Group, Stack, Text, Title } from '@mantine/core';

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
  return (
    <Card className="glass" p="xl">
      <Title order={5} mb="sm">
        Версии
      </Title>
      {view.history.length === 0 ? (
        <Text size="sm" c="dimmed">
          Версий ещё нет — агент на этом этапе не пишет. Первая появится здесь после сохранения,
          вместе с автором и датой.
        </Text>
      ) : (
        <Stack gap="xs">
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
      )}
    </Card>
  );
}
