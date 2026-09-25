/**
 * Строка очереди прогона: домен, по каким ключам нашёлся, что сказал судья,
 * что ответил донор, и решение человека.
 *
 * **Ярлык судьи есть всегда.** Даже когда человек уже решил: по расхождению
 * решения с ярлыком меряется точность судьи, и прятать ярлык после решения
 * значит прятать то, по чему судье когда-нибудь доверят приём.
 *
 * **«Продаёт размещение» — один раз, в колонке своего источника.** До
 * 25.09.2026 строка печатала это трижды: значком у домена, пояснением
 * «судья: продаёт размещение у себя» рядом и ярлыком судьи. Сервер называет,
 * чей голос это сказал (`sells_by`): ответ сайта виден в «Донор ответил»,
 * тип сайта по судье — у судьи, и у домена признак стоит, только если его
 * больше нигде нет — решение человека или дверь на сайте.
 *
 * **Колонки «Нашёлся по ключам» нет, если прогон этого не хранит** — вместо
 * столбца прочерков пояснение стоит наверху экрана.
 */

import { Anchor, Badge, Button, Checkbox, Group, Stack, Table, Text, Tooltip } from '@mantine/core';

import { CONTACT_STATUSES, countryTitle, REVIEW_TIERS } from '../api/labels';
import type { ReviewCandidate, ReviewDecision } from '../api/types';
import { JudgeVerdict, SellerAnswer } from '../components/JudgeVerdict';
import { formatCompact } from '../format';

interface Props {
  row: ReviewCandidate;
  mayDecide: boolean;
  /** Хранит ли прогон, по каким ключам нашёлся домен: нет — нет и колонки. */
  withKeywords: boolean;
  busy: boolean;
  checked: boolean;
  onCheck: (checked: boolean) => void;
  onDecide: (decision: ReviewDecision) => void;
}

const KEYWORDS_SHOWN = 2;

/** Что стоит рядом со значком «продаёт размещение» у домена. Ответ сайта
 *  и тип по судье здесь не повторяются: они видны в своих колонках. */
function sellsNote(row: ReviewCandidate): string | null {
  if (row.sells_by === 'human') return 'так решил человек';
  if (row.sells_by === 'door') return row.sells;
  return null;
}

function Site({ row }: { row: ReviewCandidate }) {
  const share = row.geo_top_share === null ? null : Math.round(row.geo_top_share * 100);
  const note = sellsNote(row);
  return (
    <Stack gap={2}>
      <Anchor href={`https://${row.host}`} target="_blank" rel="noreferrer" fw={500}>
        {row.host}
      </Anchor>
      <Text size="xs" c="dimmed">
        DR {row.dr ?? '—'} · трафик {formatCompact(row.org_traffic)}
        {row.geo !== null && ` · ${countryTitle(row.geo)}${share === null ? '' : ` ${share}%`}`}
      </Text>
      {/* Почему строка стоит первой в ярусе: для гест-постинга это главный
          признак донора, и человек должен видеть, на чём он держится. */}
      {note !== null && (
        <Group gap={6} wrap="wrap" data-sells>
          <Badge variant="light" color="green" size="sm">
            продаёт размещение
          </Badge>
          <Text size="xs" c="dimmed">
            {note}
          </Text>
        </Group>
      )}
      {row.carried && (
        <Badge variant="outline" color="gray" size="sm">
          решено раньше
        </Badge>
      )}
    </Stack>
  );
}

/** По каким ключам нашёлся домен: по этому видно, какие ключи дают доноров. */
function FoundBy({ keywords }: { keywords: string[] }) {
  if (keywords.length === 0) {
    return (
      <Text size="xs" c="dimmed">
        —
      </Text>
    );
  }
  const shown = keywords.slice(0, KEYWORDS_SHOWN);
  const rest = keywords.length - shown.length;
  return (
    // Метка — для замера контраста: колонки может не быть вовсе, и проба
    // по номеру ячейки мерила бы тогда соседнюю колонку.
    <Stack gap={2} data-found-by>
      {shown.map((keyword) => (
        <Text key={keyword} size="xs">
          {keyword}
        </Text>
      ))}
      {rest > 0 && (
        <Tooltip label={keywords.slice(KEYWORDS_SHOWN).join(', ')} withArrow multiline w={280}>
          <Text size="xs" c="dimmed">
            и ещё {rest}
          </Text>
        </Tooltip>
      )}
    </Stack>
  );
}

function Decision({ row, mayDecide, busy, onDecide }: Props) {
  if (row.status !== 'pending') {
    const contact = row.contact_status === null ? null : CONTACT_STATUSES[row.contact_status];
    return (
      <Stack gap={4} align="center">
        {row.status === 'accepted' && (
          <Badge variant="light" color={contact?.color ?? 'gray'}>
            {contact?.title ?? 'контакт ищется'}
          </Badge>
        )}
        {row.decided_by !== null && (
          <Text size="xs" c="dimmed">
            {row.decided_by}
          </Text>
        )}
        {mayDecide && (
          <Button
            size="compact-xs"
            variant="subtle"
            disabled={busy}
            onClick={() => onDecide('pending')}
          >
            Вернуть
          </Button>
        )}
      </Stack>
    );
  }
  if (!mayDecide) {
    return (
      <Text size="xs" c="dimmed">
        ждёт решения
      </Text>
    );
  }
  return (
    <Group gap={6} justify="center" wrap="nowrap">
      <Button size="compact-sm" color="green" disabled={busy} onClick={() => onDecide('accepted')}>
        Принять
      </Button>
      <Button
        size="compact-sm"
        variant="default"
        disabled={busy}
        onClick={() => onDecide('rejected')}
      >
        Отклонить
      </Button>
    </Group>
  );
}

export function CandidateRow(props: Props) {
  const { row, mayDecide, withKeywords, busy, checked, onCheck } = props;
  const tier = REVIEW_TIERS[row.tier];
  return (
    <Table.Tr>
      {mayDecide && (
        <Table.Td>
          <Checkbox
            aria-label={`Выбрать ${row.host}`}
            checked={checked}
            disabled={busy}
            onChange={(event) => onCheck(event.currentTarget.checked)}
          />
        </Table.Td>
      )}
      <Table.Td>
        <Site row={row} />
      </Table.Td>
      {withKeywords && (
        <Table.Td>
          <FoundBy keywords={row.found_by} />
        </Table.Td>
      )}
      <Table.Td>
        <Stack gap={6} align="center">
          <Badge variant="dot" color={tier.color}>
            {tier.title}
          </Badge>
          <JudgeVerdict machine={row.machine} />
        </Stack>
      </Table.Td>
      <Table.Td>
        <SellerAnswer seller={row.seller} />
      </Table.Td>
      <Table.Td>
        <Decision {...props} />
      </Table.Td>
    </Table.Tr>
  );
}
