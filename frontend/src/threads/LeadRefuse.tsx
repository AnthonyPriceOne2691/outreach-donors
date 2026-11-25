/**
 * «В отказ» — рядом с «Взять в работу», у лида рекламодателя, который ждёт человека.
 *
 * Слово владельца 25.11.2026: ответ с отказом («thanks, not interested») висел в
 * «Диалогах с рекламодателями» как «лид — ждёт человека», а снять его можно было только «Взять в
 * работу» — и лид уходил в CRM. Кнопка зовёт `POST /replies/{id}/lead/refuse`: лид перестаёт ждать
 * человека, в CRM и в выгрузку лидов не уходит, переписка — «лид в отказе». Кто и когда решил —
 * строкой «В отказе: …» под лентой и в пузыре ответа (`leadDecided`), как «В работе: …» у взятого.
 *
 * Нажатие — с подтверждением у кнопки, как «Закрыть без ответа» у продаж (`sales/SalesClose.tsx`):
 * вернуть лид из отказа экран не умеет. Итог — уведомлением, отказ — словами сервера, красным. Дальше
 * переписка, список и числа меню перечитываются по номеру переписки из нажатия: итог может прийти,
 * когда открыта уже соседняя. Перечитывание — своё, а не `useRereadThread` продаж: «Диалоги» не
 * знают экранов продаж (`.dependency-cruiser.cjs`, правило mail-does-not-know-sales).
 */

import { Button } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { refuseLead } from '../api/outreach';
import type { IncomingCard } from '../api/types';
import { ConfirmPopover } from '../components/ConfirmPopover';
import { formatDateTime } from '../format';
import { notify, refuse, settle } from '../notices';

const ASK = 'В отказ? Лид перестанет ждать человека и в CRM не уйдёт.';

/** Кто и как решил лид — строкой. Одна на разбор под лентой (`ReplyDecision`) и пузырь ответа
 *  (`ThreadFeed`): «В работе» у лида в отказе было бы неправдой. */
export function leadDecided(reply: IncomingCard): string {
  const how = reply.decision === 'refused' ? 'В отказе' : 'В работе';
  return `${how}: ${reply.reviewed_by ?? ''}, ${formatDateTime(reply.reviewed_at)}`;
}

interface Pressed {
  threadId: number;
  replyId: number;
}

/** Ключ отказа в уведомлениях — свой у каждой переписки (`notices.ts`). */
const noticeOf = (threadId: number) => `thread-lead-refuse-${threadId}`;

/** Лид перестал ждать человека: меняются переписка, строка списка и числа меню. */
const changedBy = (threadId: number) => [['thread', String(threadId)], ['threads'], ['overview']];

export function useRefuseLead() {
  const queryClient = useQueryClient();
  const reread = (threadId: number) =>
    Promise.all(changedBy(threadId).map((queryKey) => queryClient.invalidateQueries({ queryKey })));
  return useMutation({
    mutationFn: ({ replyId }: Pressed) => refuseLead(replyId),
    onSuccess: async (_refused, { threadId }) => {
      settle(noticeOf(threadId));
      notify({ message: 'Лид в отказе: человека не ждёт, в CRM не ушёл', color: 'green' });
      await reread(threadId);
    },
    onError: async (failure, { threadId }) => {
      refuse(noticeOf(threadId), { title: 'Не отправили в отказ', message: refusalOf(failure) });
      // «Уже в работе» — переписка перечитывается: под лентой встанет, кто и когда его взял.
      await reread(threadId);
    },
  });
}

interface ButtonProps {
  /** По этому ответу идёт нажатие — своё или «Взять в работу». */
  busy: boolean;
  onConfirm: () => void;
}

export function RefuseButton({ busy, onConfirm }: ButtonProps) {
  return (
    <ConfirmPopover message={ASK} confirm="Отправить в отказ" onConfirm={onConfirm}>
      {(ask) => (
        <Button variant="default" className="press" loading={busy} onClick={ask}>
          В отказ
        </Button>
      )}
    </ConfirmPopover>
  );
}
