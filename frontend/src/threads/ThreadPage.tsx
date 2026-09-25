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

import { ApiError, refusalOf } from '../api/client';
import { rowIdOf } from '../api/ids';
import { MESSAGE_STATUSES, REPLY_KINDS, THREAD_STATES } from '../api/labels';
import { fetchThread, reviewReply, takeLead } from '../api/outreach';
import type { Corridor, IncomingCard, LetterCard, MessageStatus } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { BackLink, backTo } from '../components/BackLink';
import { formatDateTime, formatMoney } from '../format';
import { corridorText, readable, uniquenessText } from '../letters/letterText';
import { PriceReview } from './PriceReview';

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

function Letter({ letter, corridor }: { letter: LetterCard; corridor: Corridor }) {
  return (
    // Та же ширина и то же стекло, что у шапки и ответов: письмо уже
    // соседей и со своим скруглением читалось как вставка из другого экрана.
    // Чьё сообщение — говорит значок, а не отступ.
    <Card className="glass" p="md">
      <Group justify="space-between" gap="xs" mb="xs">
        <Group gap="xs">
          <Badge variant="light" color="lagoon">
            {letter.step === 0 ? 'первое письмо' : `добивка ${letter.step}`}
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
}

/**
 * Ответ рекламодателя — лид. Цены в нём нет: «мы платим $300» — его расход,
 * а не цена площадки, и формы разбора здесь быть не должно (сервер её
 * отвергнет). Решение одно — кто его ведёт.
 */
function LeadAction({ incoming, canTake, busy, onTake }: LeadProps) {
  if (incoming.reviewed_at !== null) {
    return (
      <Text size="sm" c="dimmed" mt="sm">
        В работе: {incoming.reviewed_by}, {when(incoming.reviewed_at)}
      </Text>
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
}

function Incoming({ incoming, canReview, busy, onConfirm, onDecline, onTakeLead }: IncomingProps) {
  const kind = REPLY_KINDS[incoming.kind];
  const hasPrice = incoming.price_white !== null || incoming.price_grey !== null;
  // Разбирают только ответы людей: у автоответчика и отказа доставки
  // разбирать нечего.
  const reviewable = incoming.kind === 'human';
  const files = incoming.attachments ?? [];

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
          не должен выглядеть пустым. Сам файл не показываем — он пришёл
          снаружи и считается недоверенным (docs/SECURITY.md). */}
      {files.length > 0 && (
        <Group gap="xs" mt="xs">
          {files.map((file) => (
            <Badge key={file.имя} variant="light" color={file.принято ? 'gray' : 'red'}>
              {file.принято ? file.имя : `${file.имя} — не принят`}
            </Badge>
          ))}
        </Group>
      )}

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

      {incoming.lead ? (
        <LeadAction incoming={incoming} canTake={canReview} busy={busy} onTake={onTakeLead} />
      ) : reviewable ? (
        <PriceReview
          incoming={incoming}
          canReview={canReview}
          busy={busy}
          onConfirm={onConfirm}
          onDecline={onDecline}
        />
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
    onSuccess: async () => {
      // Список тоже меняется: лид перестаёт ждать человека.
      await queryClient.invalidateQueries({ queryKey: ['thread', String(id)] });
      await queryClient.invalidateQueries({ queryKey: ['threads'] });
      notifications.show({ message: 'Лид взят в работу', color: 'green' });
    },
    onError: (failure) =>
      notifications.show({ title: 'Не взяли', message: refusalOf(failure), color: 'red' }),
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
          canReview={can('prices')}
          busy={
            (confirm.isPending && confirm.variables?.replyId === incoming.id) ||
            (lead.isPending && lead.variables === incoming.id)
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
            <Title order={3}>{data.card.host}</Title>
            <Badge variant="light" color={THREAD_STATES[data.card.state].color}>
              {THREAD_STATES[data.card.state].title}
            </Badge>
          </Group>
          <Text size="sm" c="dimmed">
            {data.card.contact_email ?? 'адрес не определён'} · кампания «{data.card.campaign}»
            {data.card.stage === 'advertisers' ? ' · рекламодатель' : ''}
          </Text>
        </Stack>
      </Card>

      <Stack gap="md">{timeline.map((item) => item.node)}</Stack>

      <Card className="glass" p="md">
        <Text size="sm" c="dimmed">
          Ответ из карточки появится вместе с подключением почты. Он уйдёт с того же ящика, что вёл
          переписку: смена отправителя посреди разговора уводит письма в спам.
        </Text>
      </Card>
    </Stack>
  );
}
