/**
 * «Что увидит агент» — только включённые записи, группами по виду и языку.
 *
 * Ответ сервера считается той же выборкой, из которой пишет агент: свой
 * фильтр на экране («включённые из списка») показал бы не то, что агент
 * получит, если правила выборки разойдутся. Версия — та, что запишет черновик.
 * Окно спрашивает сервер при открытии: после правки и переключения — заново.
 */

import { Alert, Loader, Modal, Stack, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { previewKb } from '../api/sales';
import { kbKindTitle } from '../api/salesLabels';
import type { AgentView, FactGroup } from '../api/salesTypes';
import { formatNumber } from '../format';

export const KB_PREVIEW_QUERY_KEY = ['sales', 'kb', 'preview'] as const;

function KindGroup({ group }: { group: FactGroup }) {
  return (
    <Stack gap={6} className="agentGroup">
      <Title order={5}>
        {kbKindTitle(group.kind)} · {group.language}
      </Title>
      {group.facts.map((fact) => (
        <Stack key={fact.id} gap={2} className="agentFact">
          <Text size="sm" fw={500}>
            {fact.title}
          </Text>
          <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
            {fact.text}
          </Text>
          {fact.tags.length > 0 && (
            <Text size="xs" c="dimmed">
              теги: {fact.tags.join(', ')}
            </Text>
          )}
        </Stack>
      ))}
    </Stack>
  );
}

function Seen({ view }: { view: AgentView }) {
  if (view.total === 0) {
    return (
      <Text size="sm">
        Агенту не из чего писать: включённых записей нет. Включите записи в таблице или заведите
        новые.
      </Text>
    );
  }
  return (
    <Stack gap="lg">
      <Text size="sm" c="dimmed">
        Версия базы <code>{view.version}</code> · записей {formatNumber(view.total)}. Выключенных
        здесь нет — агент их не видит.
      </Text>
      {view.groups.map((group) => (
        <KindGroup key={`${group.kind}-${group.language}`} group={group} />
      ))}
    </Stack>
  );
}

export function AgentPreview({ onClose }: { onClose: () => void }) {
  const preview = useQuery({ queryKey: KB_PREVIEW_QUERY_KEY, queryFn: previewKb });
  let body;
  if (preview.data !== undefined) body = <Seen view={preview.data} />;
  else if (preview.error) {
    body = (
      <Alert color="red" title="Не показали">
        {refusalOf(preview.error)}
      </Alert>
    );
  } else body = <Loader aria-label="Спрашиваем, что видит агент" />;
  return (
    <Modal opened onClose={onClose} size="xl" title="Что увидит агент">
      {body}
    </Modal>
  );
}
