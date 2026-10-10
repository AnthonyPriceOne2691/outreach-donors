/**
 * Переписка целиком: шапка диалога, лента писем, разбор ответа и ответ собеседнику.
 *
 * **Лента — как в мессенджере** (`ThreadFeed`): наши письма справа, письма
 * собеседника слева, в своём блоке с прокруткой; в пузыре ответа — написанное
 * человеком, цитата и подпись — по раскрытию. **Поле ответа — внизу карточки ленты**
 * (`Composer`, 10.10.2026): видно сразу, со скрепкой, без кнопки «Ответить»; отвечает
 * на последний ответ человека. **Под лентой — одна форма разбора** (`ReplyDecision`)
 * для ответа, который ждёт человека или открыт кнопкой в пузыре. До 09.10.2026 у
 * каждого ответа была своя карточка со своими формами, и переписка растягивалась
 * в простыню (замечание Anthony по первому настоящему ответу донора).
 *
 * **«К списку» — над заголовком, как у карточек донора и прогона**
 * (`BackLink`), и возвращает к списку с тем же фильтром. **Номер из адреса
 * проверяется до запроса** (`rowIdOf`): «abc» уходил бы на сервер как `NaN`.
 *
 * **На широком окне переписка — правой колонкой рядом со списком**
 * (`ThreadsScreen`, с 09.10.2026): «К списку» там нет — список рядом, — а колонка
 * высотой окна: лента забирает остаток и прокручивается внутри, разбор и ответ
 * под ней всегда на виду. До этого на 1440 × 900 «Взять в работу» стояло ниже края.
 *
 * **Шапка — две строки** (аудит экранов 09.10.2026, второй круг): домен, состояние
 * и ящик — первой, адрес и кампания — второй, плашка ящика — строкой под ними.
 * Шапка в 250 px и разбор цены в 317 сжимали ленту до 192 px — 21 % окна 1440 × 900,
 * а ради ленты диалог и открывают (жалоба Anthony 09.10.2026). Рядом со списком
 * во второй строке — «Следующий ждущий →» (`NextWaiting`): разбирают подряд.
 */

import { Alert, Badge, Card, Group, Loader, Stack, Text, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useLocation, useOutletContext, useParams } from 'react-router-dom';

import { ApiError, refusalOf } from '../api/client';
import { rowIdOf } from '../api/ids';
import { threadState } from '../api/labels';
import { answerReply, fetchThread, reviewReply, sendLead, takeLead } from '../api/outreach';
import { THREAD_STAGE_NOTES } from '../api/stages';
import type { IncomingCard } from '../api/types';
import { useSession } from '../auth/AuthProvider';
import { BackLink, backTo } from '../components/BackLink';
import { Seams } from '../components/Seams';
import { Composer } from './Composer';
import { NextWaiting } from './NextWaiting';
import { ReplyDecision } from './ReplyDecision';
import { ThreadFeed } from './ThreadFeed';
import { ThreadMailLine, ThreadMailWaiting } from './ThreadMailLine';
import type { ThreadOutlet } from './ThreadsScreen';
import { activeReply, answerTarget } from './threadTimeline';
import { notify } from '../notices';

/** Диалога нет: номер негодный или такого нет в базе. */
function NoSuchThread({ back, said }: { back: string | null; said: string }) {
  return (
    <Card className="glassPanel" p="xl">
      <Stack gap={6}>
        {back !== null && <BackLink to={back}>К списку</BackLink>}
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
  const location = useLocation();
  // Правой колонкой рядом со списком — без «К списку»: список и так рядом.
  const split = useOutletContext<ThreadOutlet | undefined>()?.split === true;
  // Откуда пришли: список с фильтром из адреса. «К списку» возвращает туда
  // же, а не на весь список — иначе фильтр с главной терялся бы на первом
  // же открытом диалоге. Фильтр приходит в состоянии перехода или в самом
  // адресе диалога (`/threads/42?state=…` — так его строит список рядом).
  const back = split ? null : `/threads${backTo(location.state) || location.search}`;
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
        notify({
          message: 'Отмечено: донор не продаёт размещения — домен уходит из отбора на год',
          color: 'green',
        });
        return;
      }
      notify({
        message: result.stored_price
          ? 'Цена подтверждена и записана в карточку донора'
          : 'Подтверждено. Цены в ответе нет — в карточку донора ничего не пошло',
        color: result.stored_price ? 'green' : 'yellow',
      });
    },
    // Отказ — над полями разбора (`PriceReview`), а не уведомлением в углу: оно ложилось
    // на «Подтвердить» и «Не продаёт размещения» (проверка QA 10.10.2026).
  });
  // Отказ — о вписанном в этой переписке: в соседней поля уже другие.
  const { reset: forgetRefusal } = confirm;
  useEffect(() => forgetRefusal(), [id, forgetRefusal]);

  const lead = useMutation({
    mutationFn: (replyId: number) => takeLead(replyId),
    onSuccess: async (taken) => {
      // Список тоже меняется: лид перестаёт ждать человека.
      await queryClient.invalidateQueries({ queryKey: ['thread', String(id)] });
      await queryClient.invalidateQueries({ queryKey: ['threads'] });
      notify({
        message:
          taken.handoff === 'queued'
            ? 'Лид взят в работу и передаётся в CRM'
            : 'Лид взят в работу. Передача в CRM не настроена — лид в выгрузке CSV',
        color: 'green',
      });
    },
    onError: (failure) => notify({ title: 'Не взяли', message: refusalOf(failure), color: 'red' }),
  });

  const answer = useMutation({
    mutationFn: ({
      replyId,
      text,
      fileIds,
    }: {
      replyId: number;
      text: string;
      fileIds: number[];
    }) => answerReply(id ?? 0, replyId, text, fileIds),
    onSuccess: async (sent) => {
      await queryClient.invalidateQueries({ queryKey: ['thread', String(id)] });
      await queryClient.invalidateQueries({ queryKey: ['threads'] });
      notify({
        message: sent.real
          ? `Ответ отправлен с ${sent.sender_email}`
          : 'Ответ записан, но не ушёл: почта выключена (транспорт null)',
        color: sent.real ? 'green' : 'yellow',
      });
    },
    onError: (failure) =>
      notify({ title: 'Не отправили', message: refusalOf(failure), color: 'red' }),
  });

  const handoff = useMutation({
    mutationFn: (replyId: number) => sendLead(replyId),
    onSuccess: () => notify({ message: 'Передача в CRM поставлена', color: 'green' }),
    onError: (failure) =>
      notify({ title: 'Не передали', message: refusalOf(failure), color: 'red' }),
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
        {back !== null && <BackLink to={back}>К списку</BackLink>}
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
  // Отказ сервера по разбору этого ответа — над полями разбора, словами.
  const refusalFor = (replyId: number) =>
    confirm.isError && confirm.variables.replyId === replyId ? refusalOf(confirm.error) : null;
  const busy = (replyId: number) =>
    (confirm.isPending && confirm.variables?.replyId === replyId) ||
    (lead.isPending && lead.variables === replyId) ||
    (answer.isPending && answer.variables?.replyId === replyId) ||
    (handoff.isPending && handoff.variables === replyId);

  return (
    <Stack gap="sm" {...(split ? { className: 'threadPane' } : {})}>
      <Card className="glassPanel threadHead" px="lg" py="md">
        <Stack gap={4}>
          {back !== null && <BackLink to={back}>К списку</BackLink>}
          <Group gap="xs" justify="space-between" style={{ rowGap: 2 }}>
            <Group gap="sm" wrap="nowrap" miw={0}>
              {/* Длинный домен переносится по швам: без переноса на телефоне
                  он уходил за край карточки и обрезался (06.10.2026). */}
              <Title order={3} size="h4" className="cellName">
                <Seams text={data.card.host} />
              </Title>
              <Badge variant="light" color={threadState(data.card.state).color}>
                {threadState(data.card.state).title}
              </Badge>
            </Group>
            <ThreadMailLine mail={data.mail} />
          </Group>
          <Group gap="xs" justify="space-between" style={{ rowGap: 4 }}>
            <Text size="sm" c="dimmed">
              {data.card.contact_email ?? 'адрес не определён'} · кампания «{data.card.campaign}»
              {stageNote ? ` · ${stageNote}` : ''}
            </Text>
            <NextWaiting current={data.card.id} split={split} />
          </Group>
          <ThreadMailWaiting mail={data.mail} />
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
        fill={split}
        composer={
          // Отвечают человеку: автоответчику, отказу доставки и отписке — нет. Лиду
          // продаж — тоже: подпись и адрес допишет модуль продаж.
          can('send') && target !== null ? (
            <Composer
              key={data.card.id}
              answered={data.letters.some((letter) => letter.answers_reply_id === target.id)}
              busy={busy(target.id)}
              onSend={(text, fileIds) => answer.mutate({ replyId: target.id, text, fileIds })}
              signed={sales}
              threadId={data.card.id}
              pendingFiles={data.pending_files ?? []}
              {...(data.file_rules === undefined ? {} : { rules: data.file_rules })}
            />
          ) : null
        }
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
          refusal={refusalFor(decided.id)}
          onTakeLead={() => lead.mutate(decided.id)}
          onSendLead={() => handoff.mutate(decided.id)}
          onClose={decided.id === picked ? () => setPicked(null) : null}
        />
      )}
    </Stack>
  );
}
