/**
 * Бизнесы ниши из выдачи прогона: сайты, которые сами продают в нише,
 * — не доноры, а кандидаты в рекламодатели (решение Anthony 04.10.2026).
 *
 * Человек решает «пишем / не пишем»: до «пишем» ни поиск адреса, ни письмо
 * бизнес не трогают. Для прогонов, прошедших до сбора, — «Собрать из
 * прогона»: вердикты судьи там уже оплачены.
 */

import { Badge, Button, Card, Group, NumberInput, Stack, Text, Title } from '@mantine/core';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import type { NicheCard } from '../api/niche';
import { useSession } from '../auth/AuthProvider';
import { useNicheCollect, useNicheQueue } from './useNiche';
import { InfoHint } from '../components/InfoHint';

function whereFrom(card: NicheCard): string {
  const run = card.run_id === null ? 'прогон удалён' : `прогон №${card.run_id}`;
  const keys = card.keywords.slice(0, 3).join(', ');
  const country = card.country ? card.country.toUpperCase() : null;
  return [run, keys, country].filter(Boolean).join(' · ');
}

function NicheRow({
  card,
  canDecide,
  busy,
  onDecide,
}: {
  card: NicheCard;
  canDecide: boolean;
  busy: boolean;
  onDecide: (write: boolean) => void;
}) {
  return (
    <Stack gap={4} className="nicheRow">
      <Group justify="space-between" gap="xs">
        <Text fw={500}>{card.host}</Text>
        {canDecide && (
          <Group gap="xs">
            <Button
              size="compact-sm"
              className="press"
              loading={busy}
              onClick={() => onDecide(true)}
            >
              Пишем
            </Button>
            <Button
              size="compact-sm"
              variant="subtle"
              color="gray"
              className="press"
              disabled={busy}
              onClick={() => onDecide(false)}
            >
              Не пишем
            </Button>
          </Group>
        )}
      </Group>
      <Text size="sm" c="dimmed">
        {whereFrom(card)}
      </Text>
      <Group gap="xs">
        <Badge variant="light" color={card.intent_by === 'human' ? 'green' : 'lagoon'}>
          {card.intent_by === 'human' ? 'продаёт своё — сказал человек' : 'продаёт своё — судья'}
        </Badge>
        {card.quote && (
          <Text size="sm" className="nicheQuote">
            «{card.quote}»
          </Text>
        )}
      </Group>
    </Stack>
  );
}

function Collect() {
  const [runId, setRunId] = useState<number | ''>('');
  const collect = useNicheCollect();
  return (
    <Group gap="xs" align="flex-end">
      <NumberInput
        label="Собрать из прогона №"
        w="12rem"
        min={1}
        allowDecimal={false}
        allowNegative={false}
        value={runId}
        onChange={(value) => setRunId(typeof value === 'number' ? value : '')}
      />
      <Button
        variant="light"
        className="press"
        loading={collect.isPending}
        disabled={runId === ''}
        onClick={() => runId !== '' && collect.mutate(runId)}
      >
        Собрать
      </Button>
    </Group>
  );
}

export function NicheQueue() {
  const { can } = useSession();
  const { queue, decide } = useNicheQueue();
  const { data, error } = queue;
  const rows = data?.rows ?? [];

  return (
    <Card className="glass" p="xl">
      <Stack gap="md">
        <Stack gap={6}>
          <Group gap="xs">
            <Title order={4}>Бизнесы ниши из выдачи</Title>
            <InfoHint name="Что такое бизнесы ниши" width={340}>
              Сайты, которые сами продают в нише прогона: не доноры, а кандидаты в рекламодатели.
              Пока не решили «пишем», адрес им не ищут и писем не пишут.
            </InfoHint>
          </Group>
          {data !== undefined && <Text size="sm">{`Ждут решения: ${data.waiting}.`}</Text>}
        </Stack>
        {error ? (
          <Text size="sm" c="red">
            {refusalOf(error)}
          </Text>
        ) : rows.length === 0 ? (
          <Text size="sm" c="dimmed">
            Ждущих решения нет: бизнесы ниши собираются в конце каждого прогона.
          </Text>
        ) : (
          <Stack gap="md">
            {rows.map((card) => (
              <NicheRow
                key={card.id}
                card={card}
                canDecide={can('prices')}
                busy={decide.isPending && decide.variables?.id === card.id}
                onDecide={(write) => decide.mutate({ id: card.id, write })}
              />
            ))}
          </Stack>
        )}
        {can('prices') && <Collect />}
      </Stack>
    </Card>
  );
}
