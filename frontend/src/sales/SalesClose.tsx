/**
 * «Закрыть без ответа» — рядом с «Передать телемаркетологу», у ответа продаж, который ждёт человека.
 *
 * Слово владельца 09.11.2026: подтверждение заявки службы поддержки, принятое ответом человека, и
 * пустой ответ снять с ожидания было нечем, кроме передачи телемаркетологу, — а она завела бы
 * сделку в Kommo на службу поддержки. Кнопка зовёт `POST /sales/threads/{N}/close` с ответом,
 * который человек видит в карточке (`reply_id`): он и пришедшие раньше перестают ждать, пришедший
 * позже ждёт дальше. Сделки и сообщения нет, писем лиду и так нет — цепочку остановил его ответ.
 *
 * Нажатие — с подтверждением у кнопки; итог — уведомлением и строкой под кнопками, отказ — словами
 * сервера там же и красным уведомлением. Дальше переписка, список и числа меню перечитываются: ответ
 * больше не ждёт, карточка уходит, а под ответом лента пишет «Закрыт без ответа: <кто>».
 */

import { Button } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { ConfirmPopover } from '../components/ConfirmPopover';
import { notify, refuse, settle } from '../notices';
import { closeWithoutAnswer } from './handoffApi';

const ASK =
  'Закрыть без ответа? Переписка перестанет ждать человека. Писем продаж лиду больше не будет: ' +
  'цепочку остановил его ответ. Сделки в Kommo и сообщения в Telegram не будет.';

interface Pressed {
  threadId: number;
  replyId: number;
}

/** Перечитать переписку, список и числа меню: ответ перестал ждать человека. По номеру переписки
 *  из нажатия: итог может прийти, когда открыта уже соседняя. */
export function useRereadThread(): (threadId: number) => Promise<void> {
  const queryClient = useQueryClient();
  return async (threadId: number) => {
    await queryClient.invalidateQueries({ queryKey: ['thread', String(threadId)] });
    await queryClient.invalidateQueries({ queryKey: ['threads'] });
    await queryClient.invalidateQueries({ queryKey: ['overview'] });
  };
}

/** Ключ отказа в уведомлениях — свой у каждой переписки (`notices.ts`). */
const actionOf = (threadId: number) => `thread-close-${threadId}`;

export function useCloseWithoutAnswer() {
  const reread = useRereadThread();
  return useMutation({
    mutationFn: ({ threadId, replyId }: Pressed) => closeWithoutAnswer(threadId, replyId),
    onSuccess: async (done, { threadId }) => {
      settle(actionOf(threadId));
      notify({ message: done.said, color: 'green' });
      await reread(threadId);
    },
    onError: async (failure, { threadId }) => {
      refuse(actionOf(threadId), { title: 'Не закрыли', message: refusalOf(failure) });
      // «Уже не ждут» — переписка перечитывается: карточка уйдёт сама.
      await reread(threadId);
    },
  });
}

export type CloseMutation = ReturnType<typeof useCloseWithoutAnswer>;

/** Нажатие — только этой переписки: в соседней его итога и отказа нет. */
export function closedOn(close: CloseMutation, threadId: number) {
  if (close.variables?.threadId !== threadId) {
    return { busy: false, said: null, refusal: null };
  }
  return { busy: close.isPending, said: close.data?.said ?? null, refusal: close.error };
}

interface ButtonProps {
  busy: boolean;
  disabled: boolean;
  onConfirm: () => void;
}

export function CloseButton({ busy, disabled, onConfirm }: ButtonProps) {
  return (
    <ConfirmPopover message={ASK} confirm="Закрыть" onConfirm={onConfirm}>
      {(ask) => (
        <Button
          variant="default"
          className="press"
          loading={busy}
          disabled={disabled}
          onClick={ask}
        >
          Закрыть без ответа
        </Button>
      )}
    </ConfirmPopover>
  );
}
