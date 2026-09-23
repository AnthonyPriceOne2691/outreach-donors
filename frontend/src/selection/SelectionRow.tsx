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

import { Anchor, Badge, Button, Group, Stack, Table, Text, Tooltip } from '@mantine/core';

import { DONOR_STATUSES, HUMAN_INTENTS, JUDGE_ADVICE, JUDGE_DECIDERS } from '../api/labels';
import type { HumanIntent, SelectionCard } from '../api/types';

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
        до Ahrefs не дошёл
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

function Machine({ row }: { row: SelectionCard }) {
  const machine = row.machine;
  if (machine.recommendation === null) {
    return (
      <Text size="xs" c="dimmed">
        судья не смотрел
      </Text>
    );
  }
  const advice = JUDGE_ADVICE[machine.recommendation];
  const who = machine.decided_by === null ? null : JUDGE_DECIDERS[machine.decided_by];
  return (
    <Stack gap={4} align="center">
      <Group gap={6} justify="center" wrap="nowrap">
        <Badge variant="light" color={advice.color}>
          {advice.title}
        </Badge>
        {who !== null && (
          <Tooltip label={who.hint} withArrow>
            <Badge variant="outline" color="gray" size="sm">
              {who.title}
            </Badge>
          </Tooltip>
        )}
      </Group>
      {machine.quote !== null ? (
        <Text size="xs" fs="italic" ta="center" maw={260}>
          «{machine.quote}»
        </Text>
      ) : (
        <Text size="xs" c="dimmed" ta="center" maw={260}>
          {machine.reason}
        </Text>
      )}
      <Group gap={8} justify="center">
        {machine.source_url !== null && (
          <Anchor href={machine.source_url} target="_blank" rel="noreferrer" size="xs">
            страница
          </Anchor>
        )}
        {machine.home_shop.length > 0 && (
          <Text size="xs" c="dimmed">
            главная: {machine.home_shop.join(', ')}
          </Text>
        )}
        {machine.home_reached === false && (
          <Text size="xs" c="dimmed">
            главная не открылась
          </Text>
        )}
      </Group>
    </Stack>
  );
}

/** Ответ самого донора на письмо. Для гест-постинга — правда первого
 *  сорта: сильнее судьи и человека, и экран показывает его отдельно. */
function Seller({ row }: { row: SelectionCard }) {
  const seller = row.seller;
  if (seller.answer === null) {
    return (
      <Text size="xs" c="dimmed">
        не отвечал
      </Text>
    );
  }
  if (seller.answer === 'free') {
    return (
      <Badge variant="light" color="green">
        берёт бесплатно
      </Badge>
    );
  }
  if (seller.answer === 'declines') {
    return (
      <Badge variant="light" color="red">
        не продаёт
      </Badge>
    );
  }
  return (
    <Stack gap={2} align="center">
      <Badge variant="light" color="green">
        продаёт
      </Badge>
      {seller.price !== null && (
        <Text size="xs" c="dimmed">
          {seller.price} {seller.currency ?? ''}
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
        {chosen === null ? 'не смотрел' : HUMAN_INTENTS[chosen]}
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
          разошёлся с судьёй
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
        <Machine row={row} />
      </Table.Td>
      <Table.Td>
        <Seller row={row} />
      </Table.Td>
      <Table.Td>
        <Human {...props} />
      </Table.Td>
    </Table.Tr>
  );
}
