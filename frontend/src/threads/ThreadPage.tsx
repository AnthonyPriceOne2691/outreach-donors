/**
 * Переписка целиком: шапка диалога, лента писем, разбор ответа и ответ собеседнику.
 *
 * **Лента — как в мессенджере** (`ThreadFeed`): наши письма справа, письма
 * собеседника слева, в своём блоке с прокруткой; в пузыре ответа — написанное
 * человеком, цитата и подпись — по раскрытию. **Под лентой — одна форма разбора**
 * (`ReplyDecision`) для ответа, который ждёт человека или открыт кнопкой в пузыре,
 * и **одна форма ответа** — на последний ответ человека. До 09.10.2026 у каждого
 * ответа была своя карточка со своими формами, и переписка растягивалась
 * в простыню (замечание Anthony по первому настоящему ответу донора).
 *
 * **«К списку» — над заголовком, как у карточек донора и прогона**
 * (`BackLink`), и возвращает к списку с тем же фильтром. **Номер из адреса
 * проверяется до запроса** (`rowIdOf`): «abc» уходил бы на сервер как `NaN`.
 */

import { Alert, Badge, Card, Group, Loader, Stack, Text, Title } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useLocation, useParams } from 'react-router-dom';

import { ApiError, refusalOf } from '../api/client';
import { rowIdOf } from '../api/ids';
import { threadState } from '../api/labels';
import { answerReply, fetchThread, reviewReply, sendLead, takeLead } from '../api/outreach';
import { THREAD_STAGE_NOTES } from '../api/stages';
import type { IncomingCard } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { BackLink, backTo } from '../components/BackLink';
import { Seams } from '../components/Seams';
import { AnswerBox } from './AnswerBox';
import { ReplyDecision } from './ReplyDecision';
import { ThreadFeed } from './ThreadFeed';
import { ThreadMailLine } from './ThreadMailLine';
import { activeReply, answerTarget } from './threadTimeline';

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
  // Ответ, открытый кнопкой в пузыре; `null` — под лентой последний ждущий человека.
  const [picked, setPicked] = useState<number | null>(null);
  useEffect(() => setPicked(null), [id]);

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
  const sales = data.card.stage === 'sales';
  // Разбирают ответы людей: у автоответчика и отказа доставки разбирать нечего.
  // Кроме автоответа с суммой в валюте — у него сервер называет причину, и без
  // формы цену из него было бы некуда вписать. У продаж формы цены нет вовсе:
  // причину ожидания называет сервер, вид ответа разбирает модуль продаж.
  const reviewable = (reply: IncomingCard) =>
    !sales && (reply.kind === 'human' || Boolean(reply.review_reason));
  const active = activeReply(data.incoming, picked);
  const decided = active !== null && (active.lead || reviewable(active)) ? active : null;
  const target = answerTarget(data.incoming);
  const busy = (replyId: number) =>
    (confirm.isPending && confirm.variables?.replyId === replyId) ||
    (lead.isPending && lead.variables === replyId) ||
    (answer.isPending && answer.variables?.replyId === replyId) ||
    (handoff.isPending && handoff.variables === replyId);

  return (
    <Stack gap="md">
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

      <ThreadFeed
        letters={data.letters}
        incoming={data.incoming}
        corridor={data.corridor}
        activeId={decided?.id ?? null}
        reviewable={reviewable}
        sales={sales}
        onPick={setPicked}
      />

      {decided !== null && (
        <ReplyDecision
          reply={decided}
          canReview={can('prices')}
          busy={busy(decided.id)}
          onConfirm={(values) => confirm.mutate({ replyId: decided.id, values })}
          onDecline={() =>
            confirm.mutate({
              replyId: decided.id,
              values: { price_white: null, price_grey: null, currency: null },
              declines: true,
            })
          }
          onTakeLead={() => lead.mutate(decided.id)}
          onSendLead={() => handoff.mutate(decided.id)}
          onClose={decided.id === picked ? () => setPicked(null) : null}
        />
      )}

      {/* Отвечают человеку: автоответчику, отказу доставки и отписке — нет. Лиду
          продаж — тоже: подпись и адрес допишет модуль продаж. */}
      {can('send') && target !== null && (
        <Card className="glass" p="md">
          <AnswerBox
            answered={data.letters.some((letter) => letter.answers_reply_id === target.id)}
            busy={busy(target.id)}
            onSend={(text) => answer.mutate({ replyId: target.id, text })}
            signed={sales}
          />
        </Card>
      )}
    </Stack>
  );
}
