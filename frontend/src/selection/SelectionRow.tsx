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
 *
 * **Строка, которую решение увело на другую вкладку, остаётся на месте**
 * (`useDecisions`, проверка QA 10.10.2026) — решение и куда ушла одной
 * пометкой, рядом «Вернуть»: нижние строки не поднимаются под курсор.
 */

import { Anchor, Badge, Button, Group, Stack, Table, Text } from '@mantine/core';

import {
  DONOR_STATUSES,
  HUMAN_INTENTS,
  NOT_REACHED,
  SELECTION_HUMAN,
  SELECTION_TABS,
} from '../api/labels';
import type { HumanIntent, SelectionCard } from '../api/types';
import { JudgeVerdict, SellerAnswer } from '../components/JudgeVerdict';

interface Props {
  row: SelectionCard;
  /** Есть ли на вкладке колонка «Пороги»: у принятых её нет. */
  withThresholds: boolean;
  mayDecide: boolean;
  busy: boolean;
  /** Решение увело строку с вкладки — она стоит на месте до смены вида. */
  gone: boolean;
  onDecide: (row: SelectionCard, intent: HumanIntent | null) => void;
  onUndo: (row: SelectionCard) => void;
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

/** Ушедшая строка: решение и вкладка, куда оно её увело, и «Вернуть» — к тому,
 *  что было до нажатия. Одной строкой вместо трёх кнопок: пометка под кнопками
 *  растила строку на 23 px и сдвигала нижние вниз — та же ловушка под курсором,
 *  только в другую сторону (замер в браузере 10.10.2026). */
function Gone({ row, busy, onUndo }: Pick<Props, 'row' | 'busy' | 'onUndo'>) {
  const tab = SELECTION_TABS[row.tab];
  const said = row.human.intent === null ? 'решение снято' : HUMAN_INTENTS[row.human.intent];
  return (
    <Group gap={6} justify="center" wrap="nowrap">
      <Badge variant="light" color={tab.color} size="sm">
        {said} → «{tab.title}»
      </Badge>
      <Button
        size="compact-xs"
        variant="subtle"
        className="press"
        disabled={busy}
        onClick={() => onUndo(row)}
      >
        Вернуть
      </Button>
    </Group>
  );
}

function Human({ row, mayDecide, busy, gone, onDecide, onUndo }: Props) {
  const chosen = row.human.intent;
  if (!mayDecide) {
    return (
      <Text size="sm" c={chosen === null ? 'dimmed' : 'inherit'}>
        {chosen === null ? SELECTION_HUMAN.unreviewed : HUMAN_INTENTS[chosen]}
      </Text>
    );
  }
  if (gone) return <Gone row={row} busy={busy} onUndo={onUndo} />;
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
  const { row, withThresholds } = props;
  return (
    <Table.Tr>
      <Table.Td>
        {/* Домен и DR — одной строкой: строка таблицы ~48 px, а не 67 при двадцати
            строках на странице (аудит экранов 09.10.2026). Обещание держит только
            строка без переноса: со швами и переносом ряда на 1440 с раскрытым
            меню (колонка 217 px) DR уходил под домен, а длинный домен ломался по
            точкам в три строки — 91 px (проверка QA 10.10.2026). Не влезло —
            многоточие; целиком домен — в подсказке и в имени ссылки. */}
        <Group gap={8} justify="center" wrap="nowrap">
          <Anchor
            href={`https://${row.host}`}
            target="_blank"
            rel="noreferrer"
            fw={500}
            truncate="end"
            title={row.host}
            className="cellName"
          >
            {row.host}
          </Anchor>
          {row.dr !== null && (
            <Text size="xs" c="dimmed" style={{ whiteSpace: 'nowrap', flexShrink: 0 }}>
              DR {row.dr}
            </Text>
          )}
        </Group>
      </Table.Td>
      {withThresholds && (
        <Table.Td data-label="Пороги">
          <Thresholds row={row} />
        </Table.Td>
      )}
      <Table.Td data-label="Судья">
        <JudgeVerdict machine={row.machine} />
      </Table.Td>
      <Table.Td data-label="Донор ответил">
        <SellerAnswer seller={row.seller} />
      </Table.Td>
      <Table.Td className="cellDecide" data-label="Человек">
        <Human {...props} />
      </Table.Td>
    </Table.Tr>
  );
}
