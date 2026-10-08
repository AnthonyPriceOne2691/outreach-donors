/**
 * Переписка целиком.
 *
 * Письма идут подряд, как в мессенджере. **Рядом с ответом — то, что
 * из него распознали: цена белая, цена серая, способы оплаты. Именно
 * рядом, а не вместо**: человек должен видеть, откуда взялось число,
 * иначе проверить его нечем.
 *
 * Отметки доставки — одна галочка и две: принято платформой и доставлено
 * на сервер получателя. Прочитано не показываем: отслеживание открытий
 * требует картинки-маячка, а она сама по себе повод уйти в спам.
 *
 * **«К списку» — над заголовком, как у карточек донора и прогона**
 * (`BackLink`), и возвращает к списку с тем же фильтром. **Номер из адреса
 * проверяется до запроса** (`rowIdOf`): «abc» уходил бы на сервер как `NaN`.
 */

import {
  Alert,
  Badge,
  Button,
  Card,
  Divider,
  Group,
  Loader,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { IconCheck, IconChecks } from '@tabler/icons-react';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useLocation, useParams } from 'react-router-dom';

import { AgentDraftBanner } from '../agent/AgentDraftBanner';
import { ApiError, refusalOf } from '../api/client';
import { rowIdOf } from '../api/ids';
import { MESSAGE_STATUSES, REPLY_KINDS, threadState } from '../api/labels';
import { answerReply, fetchThread, reviewReply, sendLead, takeLead } from '../api/outreach';
import { THREAD_STAGE_NOTES } from '../api/stages';
import type { Corridor, IncomingCard, LetterCard, MessageStatus } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { BackLink, backTo } from '../components/BackLink';
import { Seams } from '../components/Seams';
import { formatDateTime, formatMoney } from '../format';
import { corridorText, readable, uniquenessText } from '../letters/letterText';
import { AnswerBox } from './AnswerBox';
import { PriceReview } from './PriceReview';
import { ReplyFiles } from './ReplyFiles';
import { ReplyOffers } from './ReplyOffers';
import { ThreadMailLine } from './ThreadMailLine';

const when = formatDateTime;

/** Одна галочка — принято платформой, две — доставлено получателю. */
function DeliveryMark({ status }: { status: MessageStatus }) {
  if (status === 'delivered') {
    return <IconChecks size={16} aria-label="доставлено" />;
  }
  if (status === 'sent') {
    return <IconCheck size={16} aria-label="принято платформой" />;
  }
  return null;
}

/** Чьё и какое письмо: первое, добивка или наш ответ на ответ собеседника. */
function letterTitle(letter: LetterCard): string {
  if (letter.answers_reply_id !== null) return 'наш ответ';
  return letter.step === 0 ? 'первое письмо' : `добивка ${letter.step}`;
}

function Letter({ letter, corridor }: { letter: LetterCard; corridor: Corridor }) {
  return (
    // Та же ширина и то же стекло, что у шапки и ответов: письмо уже
    // соседей и со своим скруглением читалось как вставка из другого экрана.
    // Чьё сообщение — говорит значок, а не отступ.
    <Card className="glass" p="md">
      <Group justify="space-between" gap="xs" mb="xs">
        <Group gap="xs">
          <Badge variant="light" color="lagoon">
            {letterTitle(letter)}
          </Badge>
          <Text size="xs" c="dimmed">
            {MESSAGE_STATUSES[letter.status]}
          </Text>
          <DeliveryMark status={letter.status} />
        </Group>
        <Text size="xs" c="dimmed">
          {when(letter.sent_at)}
        </Text>
      </Group>
      {letter.subject !== null && (
        <Text fw={500} mb={4}>
          {readable(letter.subject).text}
        </Text>
      )}
      {/* Громкая метка незаданной подписи — тихой пометкой, как на экране писем. */}
      <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
        {readable(letter.body).text}
      </Text>
      {/* Доля, а не проценты: сервер отдаёт 0–1, как у письма в очереди.
          Прежде здесь печаталось «0%» у письма с отличием 19%. Коридор —
          с сервера, слово — то же, что на экране писем. */}
      {letter.uniqueness !== null && (
        <Text size="xs" c="dimmed" mt="xs">
          Отличие от шаблона {uniquenessText(letter.uniqueness, corridor)} — коридор{' '}
          {corridorText(corridor)}
        </Text>
      )}
    </Card>
  );
}

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

interface IncomingProps {
  incoming: IncomingCard;
  canReview: boolean;
  busy: boolean;
  onConfirm: (values: {
    price_white: string | null;
    price_grey: string | null;
    currency: string | null;
  }) => void;
  onDecline: () => void;
  onTakeLead: () => void;
  canAnswer: boolean;
  answered: boolean;
  onAnswer: (text: string) => void;
  onSendLead: () => void;
  /**
   * Ответ лида продаж: ни формы цены, ни «взять лид» — вид ответа разбирает модуль
   * продаж. Ответить лиду можно и своими словами, и черновиком агента (плашка черновика):
   * оба уходят мостом почты к модулю продаж, подпись и адрес он дописывает сам, а не
   * подключены продажи или лиду писать нельзя — отказ словами сервера.
   */
  sales: boolean;
}

function Incoming({
  incoming,
  sales,
  canReview,
  busy,
  onConfirm,
  onDecline,
  onTakeLead,
  canAnswer,
  answered,
  onAnswer,
  onSendLead,
}: IncomingProps) {
  const kind = REPLY_KINDS[incoming.kind];
  const hasPrice = incoming.price_white !== null || incoming.price_grey !== null;
  // Разбирают ответы людей: у автоответчика и отказа доставки разбирать
  // нечего. Кроме автоответа с суммой в валюте — у него сервер называет
  // причину, и без формы цену из него было бы некуда вписать.
  // У продаж нет ни того ни другого: причину ожидания называет сервер.
  const reviewable = !sales && (incoming.kind === 'human' || Boolean(incoming.review_reason));

  return (
    <Card className="glass" p="md">
      <Group justify="space-between" gap="xs" mb="xs">
        <Group gap="xs">
          <Badge variant="light" color={kind.color}>
            {kind.title}
          </Badge>
          {incoming.needs_review && (
            <Badge variant="light" color="yellow">
              ждёт разбора
            </Badge>
          )}
          {incoming.lead && (
            <Badge variant="light" color={incoming.reviewed_at === null ? 'yellow' : 'green'}>
              {incoming.reviewed_at === null ? 'лид' : 'лид в работе'}
            </Badge>
          )}
        </Group>
        <Text size="xs" c="dimmed">
          {when(incoming.received_at)}
        </Text>
      </Group>

      {incoming.from_email !== null && (
        <Text size="xs" c="dimmed" mb={4}>
          от {incoming.from_email}
        </Text>
      )}

      <Text size="sm" style={{ whiteSpace: 'pre-wrap' }}>
        {incoming.raw_body}
      </Text>

      {/* Прайс приходит файлом чаще, чем текстом: ответ с вложением
          не должен выглядеть пустым. Файл скачивается, но не показывается
          внутри страницы — он пришёл снаружи (docs/SECURITY.md). */}
      <ReplyFiles replyId={incoming.id} files={incoming.attachments} />

      {hasPrice && (
        <>
          <Divider my="sm" variant="dashed" />
          {/* Распознанное — рядом с исходным текстом, а не вместо него:
              иначе спорный разбор нечем проверить. */}
          <Group gap="sm">
            {incoming.price_white !== null && (
              <Badge variant="light" color="green">
                белая {formatMoney(incoming.price_white, incoming.currency)}
              </Badge>
            )}
            {incoming.price_grey !== null && (
              <Badge variant="light" color="yellow">
                серая {formatMoney(incoming.price_grey, incoming.currency)}
              </Badge>
            )}
            {(incoming.payment_methods ?? []).map((method) => (
              <Badge key={method} variant="light" color="gray">
                {method}
              </Badge>
            ))}
          </Group>
        </>
      )}
      <ReplyOffers offers={incoming.offers} />

      {incoming.lead ? (
        <LeadAction
          incoming={incoming}
          canTake={canReview}
          busy={busy}
          onTake={onTakeLead}
          onSend={onSendLead}
        />
      ) : reviewable ? (
        <PriceReview
          incoming={incoming}
          canReview={canReview}
          busy={busy}
          onConfirm={onConfirm}
          onDecline={onDecline}
        />
      ) : null}

      {sales && incoming.review_reason ? (
        <Text size="sm" c="dimmed" mt="sm">
          Ждёт человека: {incoming.review_reason}.
        </Text>
      ) : null}

      <AgentDraftBanner replyId={incoming.id} />
      {/* Отвечают человеку: автоответчику, отказу доставки и отписке — нет. Лиду
          продаж — тоже: до 08.10.2026 формы у него не было, и с выключенным агентом
          продаж на вопрос лида ответить было нечем. */}
      {canAnswer && incoming.kind === 'human' ? (
        <AnswerBox answered={answered} busy={busy} onSend={onAnswer} signed={sales} />
      ) : null}
    </Card>
  );
}

/** Диалога нет: номер негодный или такого нет в базе. */
function NoSuchThread({ back, said }: { back: string; said: string }) {
  return (
    <Card className="glassPanel" p="xl">
      <Stack gap={6}>
        <BackLink to={back}>К списку</BackLink>
        <Title order={3}>Такого диалога нет</Title>
        <Text size="sm" c="dimmed" maw={720}>
          {said} Все диалоги — в списке, переписка открывается щелчком по строке.
        </Text>
      </Stack>
    </Card>
  );
}

export function ThreadPage() {
  const raw = useParams<{ id: string }>().id;
  const id = rowIdOf(raw);
  // Откуда пришли: список с фильтром из адреса. «К списку» возвращает туда
  // же, а не на весь список — иначе фильтр с главной терялся бы на первом
  // же открытом диалоге.
  const back = `/threads${backTo(useLocation().state)}`;
  const { can } = useSession();
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ['thread', String(id)],
    queryFn: () => fetchThread(id ?? 0),
    enabled: id !== null,
  });

  const confirm = useMutation({
    mutationFn: ({
      replyId,
      values,
      declines = false,
    }: {
      replyId: number;
      values: { price_white: string | null; price_grey: string | null; currency: string | null };
      declines?: boolean;
    }) =>
      reviewReply(replyId, { ...values, payment_methods: [], ...(declines ? { declines } : {}) }),
    onSuccess: async (result) => {
      // Список диалогов тоже меняется: состояние «ждёт разбора» уходит.
      await queryClient.invalidateQueries({ queryKey: ['thread', String(id)] });
      await queryClient.invalidateQueries({ queryKey: ['threads'] });
      if (result.seller_answer === 'declines') {
        notifications.show({
          message: 'Отмечено: донор не продаёт размещения — домен уходит из отбора на год',
          color: 'green',
        });
        return;
      }
      notifications.show({
        message: result.stored_price
          ? 'Цена подтверждена и записана в карточку донора'
          : 'Подтверждено. Цены в ответе нет — в карточку донора ничего не пошло',
        color: result.stored_price ? 'green' : 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не подтвердили', message: refusalOf(failure), color: 'red' }),
  });

  const lead = useMutation({
    mutationFn: (replyId: number) => takeLead(replyId),
    onSuccess: async (taken) => {
      // Список тоже меняется: лид перестаёт ждать человека.
      await queryClient.invalidateQueries({ queryKey: ['thread', String(id)] });
      await queryClient.invalidateQueries({ queryKey: ['threads'] });
      notifications.show({
        message:
          taken.handoff === 'queued'
            ? 'Лид взят в работу и передаётся в CRM'
            : 'Лид взят в работу. Передача в CRM не настроена — лид в выгрузке CSV',
        color: 'green',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не взяли', message: refusalOf(failure), color: 'red' }),
  });

  const answer = useMutation({
    mutationFn: ({ replyId, text }: { replyId: number; text: string }) =>
      answerReply(id ?? 0, replyId, text),
    onSuccess: async (sent) => {
      await queryClient.invalidateQueries({ queryKey: ['thread', String(id)] });
      await queryClient.invalidateQueries({ queryKey: ['threads'] });
      notifications.show({
        message: sent.real
          ? `Ответ отправлен с ${sent.sender_email}`
          : 'Ответ записан, но не ушёл: почта выключена (транспорт null)',
        color: sent.real ? 'green' : 'yellow',
      });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не отправили', message: refusalOf(failure), color: 'red' }),
  });

  const handoff = useMutation({
    mutationFn: (replyId: number) => sendLead(replyId),
    onSuccess: () => notifications.show({ message: 'Передача в CRM поставлена', color: 'green' }),
    onError: (failure) =>
      notifications.show({ title: 'Не передали', message: refusalOf(failure), color: 'red' }),
  });

  if (id === null) {
    return <NoSuchThread back={back} said={`«${raw ?? ''}» в адресе — не номер диалога.`} />;
  }
  if (error instanceof ApiError && error.status === 404) {
    return <NoSuchThread back={back} said={`${refusalOf(error)}.`} />;
  }
  if (isLoading) return <Loader aria-label="Загружаем переписку" m="md" />;
  if (error) {
    return (
      <Stack gap="lg">
        <BackLink to={back}>К списку</BackLink>
        <Alert color="red" title="Переписка не загрузилась">
          {refusalOf(error)}
        </Alert>
      </Stack>
    );
  }
  if (data === undefined) return null;
  const stageNote = THREAD_STAGE_NOTES[data.card.stage];

  // Письма и входящие идут одной лентой по времени — так же, как их
  // читал бы человек в почте.
  const timeline = [
    ...data.letters.map((letter) => ({
      at: letter.sent_at ?? '',
      node: <Letter key={`letter-${letter.id}`} letter={letter} corridor={data.corridor} />,
    })),
    ...data.incoming.map((incoming) => ({
      at: incoming.received_at,
      node: (
        <Incoming
          key={`incoming-${incoming.id}`}
          incoming={incoming}
          sales={data.card.stage === 'sales'}
          canReview={can('prices')}
          busy={
            (confirm.isPending && confirm.variables?.replyId === incoming.id) ||
            (lead.isPending && lead.variables === incoming.id) ||
            (answer.isPending && answer.variables?.replyId === incoming.id) ||
            (handoff.isPending && handoff.variables === incoming.id)
          }
          onConfirm={(values) => confirm.mutate({ replyId: incoming.id, values })}
          onDecline={() =>
            confirm.mutate({
              replyId: incoming.id,
              values: { price_white: null, price_grey: null, currency: null },
              declines: true,
            })
          }
          onTakeLead={() => lead.mutate(incoming.id)}
          canAnswer={can('send')}
          answered={data.letters.some((letter) => letter.answers_reply_id === incoming.id)}
          onAnswer={(text) => answer.mutate({ replyId: incoming.id, text })}
          onSendLead={() => handoff.mutate(incoming.id)}
        />
      ),
    })),
  ].sort((a, b) => a.at.localeCompare(b.at));

  return (
    <Stack gap="lg">
      <Card className="glassPanel" p="xl">
        <Stack gap={6}>
          <BackLink to={back}>К списку</BackLink>
          <Group gap="sm">
            {/* Длинный домен переносится по швам: без переноса на телефоне
                он уходил за край карточки и обрезался (06.10.2026). */}
            <Title order={3} className="cellName">
              <Seams text={data.card.host} />
            </Title>
            <Badge variant="light" color={threadState(data.card.state).color}>
              {threadState(data.card.state).title}
            </Badge>
          </Group>
          <Text size="sm" c="dimmed">
            {data.card.contact_email ?? 'адрес не определён'} · кампания «{data.card.campaign}»
            {stageNote ? ` · ${stageNote}` : ''}
          </Text>
          <ThreadMailLine mail={data.mail} />
        </Stack>
      </Card>

      <Stack gap="md">{timeline.map((item) => item.node)}</Stack>
    </Stack>
  );
}
