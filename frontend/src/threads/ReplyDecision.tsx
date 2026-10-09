/**
 * Разбор ответа под лентой: цена донора или лид рекламодателя — для одного ответа.
 *
 * **Одна форма на переписку, а не у каждого ответа.** Форма цены у каждого письма
 * растягивала переписку в простыню (09.10.2026). Какой ответ разбирать, решает
 * лента (`threadTimeline.activeReply`): последний ждущий человека — или открытый
 * кнопкой в его пузыре («Поправить цену», «Передача лида»). Открытый кнопкой
 * сворачивается обратно; ждущий человека — нет: решение по нему ещё не принято.
 */

import { Button, Card, Group, Stack, Text } from '@mantine/core';

import type { IncomingCard } from '../api/types';
import { formatDateTime } from '../format';
import { PriceReview, type PriceReviewProps } from './PriceReview';

const when = formatDateTime;

interface LeadProps {
  incoming: IncomingCard;
  canTake: boolean;
  busy: boolean;
  onTake: () => void;
  onSend: () => void;
}

/**
 * Ответ рекламодателя — лид. Цены в нём нет: «мы платим $300» — его расход,
 * а не цена площадки, и формы разбора здесь быть не должно (сервер её
 * отвергнет). Решение одно — кто его ведёт.
 */
function LeadAction({ incoming, canTake, busy, onTake, onSend }: LeadProps) {
  if (incoming.reviewed_at !== null) {
    return (
      <Group gap="sm" mt="sm" justify="space-between">
        <Text size="sm" c="dimmed">
          В работе: {incoming.reviewed_by}, {when(incoming.reviewed_at)}
        </Text>
        {/* Передача в CRM уходит сама при «взять»; кнопка — на случай, когда
            вебхук настроили позже или получатель лежал дольше повторов. */}
        {canTake ? (
          <Button variant="subtle" size="xs" loading={busy} onClick={onSend}>
            Передать в CRM ещё раз
          </Button>
        ) : null}
      </Group>
    );
  }
  return (
    <Stack gap="xs" mt="sm">
      <Text size="sm" c="dimmed">
        Ответ рекламодателя — лид: цену в нём не разбираем, его ведёт человек.
      </Text>
      {canTake ? (
        <Group>
          <Button color="lagoon" className="press" loading={busy} onClick={onTake}>
            Взять в работу
          </Button>
        </Group>
      ) : null}
    </Stack>
  );
}

interface DecisionProps extends Omit<PriceReviewProps, 'incoming'> {
  reply: IncomingCard;
  onTakeLead: () => void;
  onSendLead: () => void;
  /** Открыт кнопкой в пузыре — можно свернуть. `null` — ответ ждёт решения. */
  onClose: (() => void) | null;
}

export function ReplyDecision({
  reply,
  canReview,
  busy,
  onConfirm,
  onDecline,
  onTakeLead,
  onSendLead,
  onClose,
}: DecisionProps) {
  const who = reply.from_email ?? 'собеседника';
  return (
    <Card className="glass" p="md" aria-label={reply.lead ? 'Лид' : 'Разбор цены'}>
      <Group justify="space-between" gap="xs" wrap="nowrap">
        <Text size="sm" fw={600}>
          {reply.lead ? 'Лид' : 'Разбор цены'} · ответ {who}, {when(reply.received_at)}
        </Text>
        {onClose !== null && (
          <Button variant="subtle" size="compact-xs" onClick={onClose}>
            Свернуть
          </Button>
        )}
      </Group>
      {reply.lead ? (
        <LeadAction
          incoming={reply}
          canTake={canReview}
          busy={busy}
          onTake={onTakeLead}
          onSend={onSendLead}
        />
      ) : (
        <PriceReview
          incoming={reply}
          canReview={canReview}
          busy={busy}
          onConfirm={onConfirm}
          onDecline={onDecline}
        />
      )}
    </Card>
  );
}
