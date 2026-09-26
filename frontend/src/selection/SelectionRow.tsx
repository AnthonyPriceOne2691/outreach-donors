/**
 * Строка отбора: домен, пороги, судья, человек — слева направо в том
 * порядке, в каком домен проходил отбор.
 *
 * **Отказ называет автора и основание.** Пороги — своими числами
 * («органический трафик 100 ниже 500»), судья — дословной цитатой и
 * страницей, по которой судил. Без цитаты вердикт нельзя проверить,
 * а экран существует ради проверки.
 *
 * **Решение человека — три кнопки, а не «годен / не годен».** Тип сайта
 * даёт расхождение по каждому слою судьи; выбранная кнопка залита, чтобы
 * решение было видно без чтения подписи.
 */

import { Anchor, Badge, Button, Group, Stack, Table, Text } from '@mantine/core';

import { DONOR_STATUSES, HUMAN_INTENTS, NOT_REACHED, SELECTION_HUMAN } from '../api/labels';
import type { HumanIntent, SelectionCard } from '../api/types';
import { JudgeVerdict, SellerAnswer } from '../components/JudgeVerdict';

interface Props {
  row: SelectionCard;
  mayDecide: boolean;
  busy: boolean;
  onDecide: (row: SelectionCard, intent: HumanIntent | null) => void;
}

const INTENTS = Object.keys(HUMAN_INTENTS) as HumanIntent[];

function Thresholds({ row }: { row: SelectionCard }) {
  if (row.status === null) {
    return (
      <Text size="xs" c="dimmed">
        {NOT_REACHED}
      </Text>
    );
  }
  return (
    <Stack gap={4} align="center">
      <Badge variant="light" color={DONOR_STATUSES[row.status].color}>
        {DONOR_STATUSES[row.status].title}
      </Badge>
      {row.reject_reason !== null && (
        <Text size="xs" c="dimmed" ta="center">
          {row.reject_reason}
        </Text>
      )}
    </Stack>
  );
}

function Human({ row, mayDecide, busy, onDecide }: Props) {
  const chosen = row.human.intent;
  if (!mayDecide) {
    return (
      <Text size="sm" c={chosen === null ? 'dimmed' : 'inherit'}>
        {chosen === null ? SELECTION_HUMAN.unreviewed : HUMAN_INTENTS[chosen]}
      </Text>
    );
  }
  return (
    <Stack gap={4} align="center">
      <Group gap={4} justify="center" wrap="nowrap">
        {INTENTS.map((intent) => (
          <Button
            key={intent}
            size="compact-xs"
            variant={chosen === intent ? 'filled' : 'default'}
            aria-pressed={chosen === intent}
            disabled={busy}
            onClick={() => onDecide(row, chosen === intent ? null : intent)}
          >
            {HUMAN_INTENTS[intent]}
          </Button>
        ))}
      </Group>
      {row.disagrees && (
        <Badge variant="light" color="yellow" size="sm">
          {SELECTION_HUMAN.disagrees}
        </Badge>
      )}
    </Stack>
  );
}

export function SelectionRow(props: Props) {
  const { row } = props;
  return (
    <Table.Tr>
      <Table.Td>
        <Anchor href={`https://${row.host}`} target="_blank" rel="noreferrer" fw={500}>
          {row.host}
        </Anchor>
        {row.dr !== null && (
          <Text size="xs" c="dimmed">
            DR {row.dr}
          </Text>
        )}
      </Table.Td>
      <Table.Td>
        <Thresholds row={row} />
      </Table.Td>
      <Table.Td>
        <JudgeVerdict machine={row.machine} />
      </Table.Td>
      <Table.Td>
        <SellerAnswer seller={row.seller} />
      </Table.Td>
      <Table.Td>
        <Human {...props} />
      </Table.Td>
    </Table.Tr>
  );
}
