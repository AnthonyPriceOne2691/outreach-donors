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
import { shownAddress } from '../hostName';
import { leadDecided, RefuseButton } from './LeadRefuse';
import { PriceReview, type PriceReviewProps } from './PriceReview';

const when = formatDateTime;

interface LeadProps {
  incoming: IncomingCard;
  canTake: boolean;
  busy: boolean;
  onTake: () => void;
  onRefuse: () => void;
  onSend: () => void;
}

/**
 * Ответ рекламодателя — лид. Цены в нём нет: «мы платим $300» — его расход,
 * а не цена площадки, и формы разбора здесь быть не должно (сервер её
 * отвергнет). Решение одно — кто его ведёт, или никто: «В отказ» (`LeadRefuse`,
 * слово владельца 25.11.2026).
 */
function LeadAction({ incoming, canTake, busy, onTake, onRefuse, onSend }: LeadProps) {
  if (incoming.reviewed_at !== null) {
    return (
      <Group gap="sm" mt="sm" justify="space-between">
        <Text size="sm" c="dimmed">
          {leadDecided(incoming)}
        </Text>
        {/* Передача в CRM уходит сама при «взять»; кнопка — на случай, когда
            вебхук настроили позже или получатель лежал дольше повторов. Лид в
            отказе в CRM не передают: его никто не ведёт. */}
        {canTake && incoming.decision !== 'refused' ? (
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
          <RefuseButton busy={busy} onConfirm={onRefuse} />
        </Group>
      ) : null}
    </Stack>
  );
}

interface DecisionProps extends Omit<PriceReviewProps, 'incoming'> {
  reply: IncomingCard;
  onTakeLead: () => void;
  onRefuseLead: () => void;
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
  refusal,
  onTakeLead,
  onRefuseLead,
  onSendLead,
  onClose,
}: DecisionProps) {
  const who = shownAddress(reply.from_email) ?? 'собеседника';
  const heading = `${reply.lead ? 'Лид' : 'Разбор цены'} · ответ ${who}, ${when(reply.received_at)}`;
  return (
    <Card className="glass" px="md" py="sm" aria-label={reply.lead ? 'Лид' : 'Разбор цены'}>
      {reply.lead ? (
        <>
          <Group justify="space-between" gap="xs" wrap="nowrap">
            <Text size="sm" fw={600}>
              {heading}
            </Text>
            {onClose !== null && (
              <Button variant="subtle" size="compact-xs" onClick={onClose}>
                Свернуть
              </Button>
            )}
          </Group>
          <LeadAction
            incoming={reply}
            canTake={canReview}
            busy={busy}
            onTake={onTakeLead}
            onRefuse={onRefuseLead}
            onSend={onSendLead}
          />
        </>
      ) : (
        <PriceReview
          incoming={reply}
          canReview={canReview}
          busy={busy}
          onConfirm={onConfirm}
          onDecline={onDecline}
          refusal={refusal}
          heading={heading}
          onClose={onClose}
        />
      )}
    </Card>
  );
}
