/**
 * Переписка лентой, как в мессенджере: наши письма справа, письма собеседника слева.
 *
 * **Лента живёт в своём блоке с прокруткой и открывается на последнем письме.**
 * До 09.10.2026 каждое письмо было полной карточкой во всю ширину, и переписка
 * растягивалась в простыню: до разбора ответа приходилось листать (замечание
 * Anthony по первому настоящему ответу донора, 08.10.2026).
 *
 * **В пузыре ответа — то, что написал человек.** Цитата нашего же письма и подпись
 * с трекинговыми ссылками — по раскрытию: «Показать цитату и подпись». Что отрезано,
 * решает сервер тем же правилом, что разбор цены (`fresh_body`).
 *
 * **Рядом с ответом — то, что из него разобрали**, значками: цена, оплата, все
 * названные цены. Форма разбора у каждого ответа и растягивала ленту — теперь она
 * одна, под лентой (`ReplyDecision`), а пузырь открывает её кнопкой.
 *
 * Отметки доставки — одна галочка и две: принято платформой и доставлено на сервер
 * получателя. Прочитано не показываем: отслеживание открытий требует
 * картинки-маячка, а она сама по себе повод уйти в спам.
 */

import { Badge, Button, Card, Group, ScrollArea, Stack, Text } from '@mantine/core';
import { IconCheck, IconChecks, IconClock } from '@tabler/icons-react';
import { Fragment, type ReactNode, useEffect, useMemo, useRef, useState } from 'react';

import { AgentDraftBanner } from '../agent/AgentDraftBanner';
import { MESSAGE_STATUSES, REPLY_KINDS } from '../api/labels';
import type { LetterWithFiles } from '../api/files';
import type { Corridor, IncomingCard, LetterCard, MessageStatus } from '../api/types';
import { formatDateTime, formatMoney } from '../format';
import { corridorText, readable, uniquenessText } from '../letters/letterText';
import { LetterFiles } from './LetterFiles';
import { ReplyFiles } from './ReplyFiles';
import { ReplyOffers } from './ReplyOffers';
import { feedOf, freshOf, waitsForPerson } from './threadTimeline';

const when = formatDateTime;

/** Одна галочка — принято платформой, две — доставлено; остальное — словами. */
function DeliveryMark({ status }: { status: MessageStatus }) {
  const said = MESSAGE_STATUSES[status];
  if (status === 'delivered') return <IconChecks size={16} aria-label={said} />;
  if (status === 'sent') return <IconCheck size={16} aria-label={said} />;
  if (status === 'bounced') {
    return (
      <Badge variant="light" color="red" size="sm">
        {said}
      </Badge>
    );
  }
  return (
    <Group gap={4} wrap="nowrap">
      <IconClock size={14} aria-hidden />
      <Text size="xs" c="dimmed">
        {said}
      </Text>
    </Group>
  );
}

/** Чьё и какое письмо: первое, добивка или наш ответ на ответ собеседника. */
export function letterTitle(letter: LetterCard): string {
  if (letter.answers_reply_id !== null) return 'наш ответ';
  return letter.step === 0 ? 'первое письмо' : `добивка ${letter.step}`;
}

function OurBubble({ letter, corridor }: { letter: LetterWithFiles; corridor: Corridor }) {
  return (
    <div className="bubbleRow bubbleRowOurs">
      <article className="bubble bubbleOurs" aria-label={`Наше письмо: ${letterTitle(letter)}`}>
        <Text size="xs" fw={600} className="bubbleWho">
          {letterTitle(letter)}
        </Text>
        {/* Тема — у первого письма: у добивок и ответов она та же, с «Re:». */}
        {letter.step === 0 && letter.subject !== null ? (
          <Text size="sm" fw={600} mt={2}>
            {readable(letter.subject).text}
          </Text>
        ) : null}
        {/* Громкая метка незаданной подписи — тихой пометкой, как на экране писем. */}
        <Text size="sm" className="bubbleText">
          {readable(letter.body).text}
        </Text>
        {/* Доля, а не проценты: сервер отдаёт 0–1, как у письма в очереди. Коридор —
            с сервера, слово — то же, что на экране писем. */}
        {letter.uniqueness !== null ? (
          <Text size="xs" c="dimmed" mt={4}>
            Отличие от шаблона {uniquenessText(letter.uniqueness, corridor)} — коридор{' '}
            {corridorText(corridor)}
          </Text>
        ) : null}
        <LetterFiles messageId={letter.id} files={letter.attachments} />
        <Group gap={6} justify="flex-end" wrap="nowrap" className="bubbleMeta">
          <Text size="xs" c="dimmed">
            {when(letter.sent_at)}
          </Text>
          <DeliveryMark status={letter.status} />
        </Group>
      </article>
    </div>
  );
}

/** Значки цены и оплаты из разбора. */
function priceBadges(reply: IncomingCard): ReactNode[] {
  const badges: ReactNode[] = [];
  if (reply.price_white !== null) {
    badges.push(
      <Badge key="white" variant="light" color="green">
        белая {formatMoney(reply.price_white, reply.currency)}
      </Badge>,
    );
  }
  if (reply.price_grey !== null) {
    badges.push(
      <Badge key="grey" variant="light" color="yellow">
        серая {formatMoney(reply.price_grey, reply.currency)}
      </Badge>,
    );
  }
  for (const method of reply.payment_methods ?? []) {
    badges.push(
      <Badge key={`pay-${method}`} variant="light" color="gray">
        {method}
      </Badge>,
    );
  }
  return badges;
}

/** Что разбор нашёл в ответе — рядом с текстом, а не вместо: видно, откуда взялось число. */
function Parsed({ reply, active }: { reply: IncomingCard; active: boolean }) {
  const badges = priceBadges(reply);
  // Кто подтвердил — у разбора под лентой, когда он открыт; здесь — когда закрыт.
  if (reply.reviewed_by !== null && !reply.lead && !active) {
    badges.push(
      <Badge key="confirmed" variant="light" color="green">
        подтвердил {reply.reviewed_by}
      </Badge>,
    );
  }
  return (
    <>
      {badges.length > 0 ? (
        <Group gap={6} mt={8} wrap="wrap">
          {badges}
        </Group>
      ) : null}
      <ReplyOffers offers={reply.offers} />
    </>
  );
}

/** Кнопка в пузыре, открывающая разбор под лентой. Разбирать нечего — кнопки нет. */
function pickLabel(reply: IncomingCard, reviewable: boolean): string | null {
  if (reply.lead) return reply.reviewed_at === null ? 'Взять лид' : 'Передача лида';
  if (!reviewable) return null;
  return waitsForPerson(reply) ? 'Разобрать' : 'Поправить цену';
}

/** Шапка пузыря ответа: кто ответил и что с ответом — значками. */
function ReplyHead({ reply }: { reply: IncomingCard }) {
  const kind = REPLY_KINDS[reply.kind];
  const taken = reply.reviewed_at !== null;
  return (
    <Group gap={6} mb={4} wrap="wrap">
      <Text size="xs" fw={600} className="bubbleWho">
        {reply.from_email ?? 'собеседник'}
      </Text>
      {reply.kind !== 'human' && (
        <Badge variant="light" color={kind.color} size="sm">
          {kind.title}
        </Badge>
      )}
      {waitsForPerson(reply) && !reply.lead && (
        <Badge variant="light" color="yellow" size="sm">
          ждёт разбора
        </Badge>
      )}
      {reply.lead && (
        <Badge variant="light" color={taken ? 'green' : 'yellow'} size="sm">
          {taken ? 'лид в работе' : 'лид'}
        </Badge>
      )}
    </Group>
  );
}

/** Текст ответа: написанное человеком, письмо целиком — по раскрытию. */
function ReplyText({ reply }: { reply: IncomingCard }) {
  const [whole, setWhole] = useState(false);
  const shown = freshOf(reply);
  return (
    <>
      <Text size="sm" className="bubbleText">
        {whole ? reply.raw_body : shown.text}
      </Text>
      {shown.cut && (
        <Button
          variant="subtle"
          size="compact-xs"
          mt={4}
          aria-expanded={whole}
          onClick={() => setWhole(!whole)}
        >
          {whole ? 'Скрыть цитату и подпись' : 'Показать цитату и подпись'}
        </Button>
      )}
    </>
  );
}

/** Пометки под текстом: почему ответ продаж ждёт человека и кто ведёт взятый лид. */
function ReplyNotes({
  reply,
  active,
  sales,
}: {
  reply: IncomingCard;
  active: boolean;
  sales: boolean;
}) {
  return (
    <>
      {sales && reply.review_reason ? (
        <Text size="sm" c="dimmed" mt="xs">
          Ждёт человека: {reply.review_reason}.
        </Text>
      ) : null}
      {/* Кто ведёт взятый лид — видно без раскрытия разбора; у открытого разбора
          это строка под лентой, второй раз её не печатаем. */}
      {reply.lead && reply.reviewed_at !== null && !active ? (
        <Text size="sm" c="dimmed" mt="xs">
          В работе: {reply.reviewed_by}, {when(reply.reviewed_at)}
        </Text>
      ) : null}
    </>
  );
}

interface TheirProps {
  reply: IncomingCard;
  /** Этот ответ разбирают под лентой — пузырь подсвечен. */
  active: boolean;
  /** К ответу применима форма цены: донорский ответ человека или автоответ с суммой. */
  reviewable: boolean;
  /** Ответ лида продаж: причину ожидания называет сервер, разбора под лентой нет. */
  sales: boolean;
  onPick: (replyId: number) => void;
}

function TheirBubble({ reply, active, reviewable, sales, onPick }: TheirProps) {
  const pick = active ? null : pickLabel(reply, reviewable);
  return (
    <div className="bubbleRow bubbleRowTheirs">
      <article
        className={active ? 'bubble bubbleTheirs bubbleActive' : 'bubble bubbleTheirs'}
        aria-label={`Ответ ${reply.from_email ?? 'собеседника'}`}
      >
        <ReplyHead reply={reply} />
        <ReplyText reply={reply} />
        <ReplyNotes reply={reply} active={active} sales={sales} />
        {/* Прайс приходит файлом чаще, чем текстом: ответ с вложением не должен
            выглядеть пустым. Файл скачивается, но не показывается внутри страницы —
            он пришёл снаружи (docs/SECURITY.md). */}
        <ReplyFiles replyId={reply.id} files={reply.attachments} />
        <Parsed reply={reply} active={active} />
        <Group justify="space-between" gap="xs" wrap="nowrap" className="bubbleMeta">
          {pick !== null ? (
            <Button variant="light" size="compact-xs" onClick={() => onPick(reply.id)}>
              {pick}
            </Button>
          ) : (
            <span />
          )}
          <Text size="xs" c="dimmed">
            {when(reply.received_at)}
          </Text>
        </Group>
      </article>
    </div>
  );
}

interface FeedProps {
  letters: LetterWithFiles[];
  incoming: IncomingCard[];
  corridor: Corridor;
  /** Ответ, который разбирают под лентой. */
  activeId: number | null;
  reviewable: (reply: IncomingCard) => boolean;
  sales: boolean;
  onPick: (replyId: number) => void;
}

export function ThreadFeed({
  letters,
  incoming,
  corridor,
  activeId,
  reviewable,
  sales,
  onPick,
}: FeedProps) {
  const viewport = useRef<HTMLDivElement>(null);
  const items = useMemo(() => feedOf(letters, incoming), [letters, incoming]);
  // Открываемся на последнем письме: разбирают и отвечают на свежее. Новое письмо
  // в ленте (ответили, пришёл ответ) — снова вниз.
  useEffect(() => {
    const node = viewport.current;
    if (node !== null) node.scrollTop = node.scrollHeight;
  }, [items.length]);
  if (items.length === 0) return null;
  return (
    <Card className="glassPanel" p={0}>
      <ScrollArea.Autosize
        mah="min(68vh, 760px)"
        type="auto"
        scrollbars="y"
        offsetScrollbars
        viewportRef={viewport}
      >
        <Stack gap="sm" p="md" className="threadFeed" aria-label="Переписка">
          {items.map((item) =>
            item.kind === 'letter' ? (
              <OurBubble
                key={`letter-${item.letter.id}`}
                letter={item.letter}
                corridor={corridor}
              />
            ) : (
              <Fragment key={`reply-${item.reply.id}`}>
                <TheirBubble
                  reply={item.reply}
                  active={item.reply.id === activeId}
                  reviewable={reviewable(item.reply)}
                  sales={sales}
                  onPick={onPick}
                />
                {/* Черновик агента — сразу за ответом, на который он написан. */}
                <AgentDraftBanner replyId={item.reply.id} />
              </Fragment>
            ),
          )}
        </Stack>
      </ScrollArea.Autosize>
    </Card>
  );
}
