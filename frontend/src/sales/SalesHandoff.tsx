/**
 * «Передать телемаркетологу» — под лентой переписки продаж, у ответа, который ждёт человека.
 *
 * Слово владельца 26.10.2026: ответ лида с телефоном модель разобрала неуверенно, ответ ждал
 * человека, а передать лида было нечем — у переписки продаж нет формы решения (`ThreadPage`:
 * `reviewable = !sales && …`). Кнопка зовёт `POST /sales/threads/{N}/handoff` — тот же путь, что
 * вид «хочет говорить»: сделка в Kommo и сообщение в Telegram уходят задачей очереди, ответы
 * переписки снимаются с ожидания словами решения.
 *
 * **Где и когда.** Карточкой под лентой, как форма решения доноров (`ReplyDecision`): одна на
 * переписку и только у ответа, который ждёт человека. У переписки без ожидания кнопки нет: ответ
 * уже разобран путём вида — отписка (звонить тому, кто просил не писать, нельзя), «не сейчас»,
 * «пишите другому» (другой человек), — а карточка под каждой такой перепиской отнимала бы место
 * у ленты (UI_RULES, 10.10.2026: лента — главное на экране). Передача уже есть — вместо кнопки
 * строка состояния «Передан телемаркетологу <дата>: Kommo — …, Telegram — …» словами сервера;
 * сбой Kommo или недоставленное сообщение — янтарной плашкой: нужно внимание.
 *
 * **Нажатие — с подтверждением у кнопки** (`ConfirmPopover`): лид уходит наружу, и писем продаж
 * ему больше нет. Пока запрос идёт — кнопка занята; итог — словами под кнопкой и уведомлением;
 * отказ — словами сервера под кнопкой и красным уведомлением, которое не гаснет само. Повтор не
 * ломает: сервер отвечает «уже передан», переписка перечитывается, на месте кнопки — строка
 * состояния. Итог и отказ — этой переписки: в соседней их нет.
 *
 * **Задача асинхронная.** Сразу после нажатия Kommo и Telegram «ждут задачи»; пока ждут, экран
 * перечитывает переписку раз в несколько секунд, но не дольше двух минут от передачи: дальше её
 * возьмёт проход по расписанию, и итог покажет следующее открытие переписки.
 *
 * Без права отправки писем кнопка закрыта, под ней — почему (`WriteRight`): право то же, что у
 * ответа в переписке.
 */

import { Alert, Button, Card, Group, Stack, Text } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { fetchThread } from '../api/outreach';
import type { ThreadView } from '../api/thread';
import type { IncomingCard } from '../api/types';
import { ConfirmPopover } from '../components/ConfirmPopover';
import { formatDateTime } from '../format';
import { notify, refuse, settle } from '../notices';
import { activeReply } from '../threads/threadTimeline';
import { handOver } from './handoffApi';
import type { SalesHandoff as Handoff, ThreadHandoff } from './handoffApi';
import { useMayWrite, WriteRight } from './WriteRight';

/** Пока Kommo или Telegram ждут задачи, переписка перечитывается так часто… */
export const SETTLE_EVERY_MS = 4000;
/** …и не дольше этого от передачи: дальше передачу берёт проход по расписанию. */
export const SETTLE_FOR_MS = 2 * 60 * 1000;

/** Состояния, на которые нужно внимание человека: Kommo не записал, сообщение не дошло. */
const TROUBLE_KOMMO: ReadonlySet<string> = new Set(['retry', 'unconfirmed', 'failed']);

const ASK =
  'Передать лида телемаркетологу? Уйдут сделка в Kommo и сообщение в Telegram; писем продаж ' +
  'лиду больше не будет, ответы переписки перестанут ждать человека.';

/** Передача ещё в пути: задача Kommo и Telegram не отработала, и прошло меньше двух минут. */
export function settling(handoff: Handoff | null): boolean {
  if (handoff === null) return false;
  const waits = handoff.kommo === 'pending' || handoff.telegram === 'pending';
  return waits && Date.now() - Date.parse(handoff.at) < SETTLE_FOR_MS;
}

/** Пока передача в пути — перечитывать переписку: итог задачи знает строка передачи. */
function useSettling(threadId: number, handoff: Handoff | null): void {
  useQuery({
    queryKey: ['thread', String(threadId)],
    queryFn: () => fetchThread(threadId),
    enabled: settling(handoff),
    refetchInterval: SETTLE_EVERY_MS,
  });
}

/** Ключ отказа в уведомлениях — свой у каждой переписки (`notices.ts`). */
const actionOf = (threadId: number) => `thread-handoff-${threadId}`;

/** Передать — с перечитыванием переписки, списка и чисел меню: ответ перестал ждать человека.
 *  Всё — по номеру переписки из нажатия: ответ может прийти, когда открыта уже соседняя. */
function useHandOver() {
  const queryClient = useQueryClient();
  async function reread(threadId: number) {
    await queryClient.invalidateQueries({ queryKey: ['thread', String(threadId)] });
    await queryClient.invalidateQueries({ queryKey: ['threads'] });
    await queryClient.invalidateQueries({ queryKey: ['overview'] });
  }
  return useMutation({
    mutationFn: (threadId: number) => handOver(threadId),
    onSuccess: async (done, threadId) => {
      settle(actionOf(threadId));
      notify({ message: done.said, color: 'green' });
      await reread(threadId);
    },
    onError: async (failure, threadId) => {
      refuse(actionOf(threadId), { title: 'Не передали', message: refusalOf(failure) });
      // «Уже передан» — переписка перечитывается: на месте кнопки встанет строка состояния.
      await reread(threadId);
    },
  });
}

function HandoffLine({ handoff }: { handoff: Handoff }) {
  const line =
    `Передан телемаркетологу ${formatDateTime(handoff.at)}: Kommo — ${handoff.kommo_words}, ` +
    `Telegram — ${handoff.telegram_words}.`;
  const trouble = TROUBLE_KOMMO.has(handoff.kommo) || handoff.telegram === 'undelivered';
  return trouble ? (
    <Alert color="yellow" py={6} px="sm">
      <Text size="sm">{line}</Text>
    </Alert>
  ) : (
    <Text size="sm">{line}</Text>
  );
}

interface AskProps {
  reply: IncomingCard;
  busy: boolean;
  onConfirm: () => void;
}

function HandoffAsk({ reply, busy, onConfirm }: AskProps) {
  const mayWrite = useMayWrite();
  return (
    <>
      <Text size="sm" fw={600}>
        Передача телемаркетологу · ответ {reply.from_email ?? 'собеседника'},{' '}
        {formatDateTime(reply.received_at)}
      </Text>
      <Group>
        <ConfirmPopover message={ASK} confirm="Передать" onConfirm={onConfirm}>
          {(ask) => (
            <Button
              color="lagoon"
              className="press"
              loading={busy}
              disabled={!mayWrite}
              onClick={ask}
            >
              Передать телемаркетологу
            </Button>
          )}
        </ConfirmPopover>
      </Group>
      {mayWrite ? null : <WriteRight what="Передаёт лида телемаркетологу" />}
    </>
  );
}

/** Итог нажатия словами — под кнопкой, словами сервера: отказ — розой, удача — приглушённо: мятные
 *  чернила на светлом стекле не держат нормы контраста (замер стенда — 4,18 : 1 на 1440), а «готово»
 *  мятой уже говорит уведомление. */
function Outcome({ said, refusal }: { said: string | null; refusal: Error | null }) {
  if (refusal !== null) {
    return (
      <Text size="sm" c="red">
        {refusalOf(refusal)}
      </Text>
    );
  }
  return said === null ? null : (
    <Text size="sm" c="dimmed">
      {said}
    </Text>
  );
}

export type HandMutation = ReturnType<typeof useHandOver>;

/** Нажатие — только этой переписки: экран переписки не пересоздаётся при выборе соседней, и итог
 *  или отказ соседней здесь не показываются. */
export function pressedOn(hand: HandMutation, threadId: number) {
  if (hand.variables !== threadId) {
    return { busy: false, said: null, refusal: null, handoff: null };
  }
  return {
    busy: hand.isPending,
    said: hand.data?.said ?? null,
    refusal: hand.error,
    handoff: hand.data?.handoff ?? null,
  };
}

export function SalesHandoff({ thread }: { thread: ThreadView }) {
  const threadId = thread.card.id;
  const hand = useHandOver();
  const pressed = pressedOn(hand, threadId);
  // Передача — из переписки; пока она не перечитана после нажатия — из ответа на нажатие.
  const handoff = (thread as ThreadView & ThreadHandoff).handoff ?? pressed.handoff;
  useSettling(threadId, handoff);
  const waiting = activeReply(thread.incoming, null);
  const body =
    handoff !== null ? (
      <HandoffLine handoff={handoff} />
    ) : waiting !== null ? (
      <HandoffAsk reply={waiting} busy={pressed.busy} onConfirm={() => hand.mutate(threadId)} />
    ) : null;
  if (body === null) return null;
  return (
    <Card
      component="section"
      className="glass"
      px="md"
      py="sm"
      aria-label="Передача телемаркетологу"
    >
      <Stack gap={8} align="flex-start">
        {body}
        <Outcome said={pressed.said} refusal={pressed.refusal} />
      </Stack>
    </Card>
  );
}
