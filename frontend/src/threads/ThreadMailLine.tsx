/**
 * Чем переписка пишет дальше: её ящик, пишет ли он и когда следующая добивка.
 *
 * Письма переписки уходят только с её ящика (`letters/mailbox.py`): ящик на
 * паузе не заменяется другим, добивка и ответ ждут его. **Почему ждут — сервер
 * говорит словами отказа отправки, и карточка показывает их до клика**: прежде
 * причина жила в журнале прохода добивок, а человек узнавал её, только нажав
 * «Ответить».
 *
 * Пусто — первое письмо ещё не уходило: ящик выберется в момент отправки.
 *
 * **Две части — строка и плашка** (`ThreadPage`): строка «Пишет …» стоит в строке
 * домена, плашка — одной строкой под шапкой, заголовок — в начале абзаца, а не
 * отдельной строкой. До 09.10.2026 шапка с плашкой занимала 250 px из 792 и
 * вместе с разбором цены сжимала ленту переписки до 192 px (аудит экранов).
 */

import { Alert, Text } from '@mantine/core';

import type { ThreadMail } from '../api/thread';
import { formatDateTime } from '../format';

/** Ящика переписки больше нет: им начата переписка, а его удалили. То же условие, что
 *  у «Ящик переписки удалён» в строке шапки: по нему поле ответа показывает причину
 *  вместо себя (`Composer.boxGone`) — ответ с другого ящика не уходит. */
export function mailboxGone(mail: ThreadMail | null | undefined): boolean {
  return mail?.mailbox === null;
}

export function ThreadMailLine({ mail }: { mail: ThreadMail | null | undefined }) {
  if (!mail) return null;
  const who = mail.mailbox === null ? 'Ящик переписки удалён' : `Пишет ${mail.mailbox}`;
  const next =
    mail.next_step !== null && mail.next_at !== null
      ? ` · добивка ${mail.next_step} — ${formatDateTime(mail.next_at)}`
      : '';
  return (
    <Text size="sm" c="dimmed">
      {who}
      {next}
    </Text>
  );
}

/** Ящик не пишет — почему письма ждут, словами сервера. */
export function ThreadMailWaiting({ mail }: { mail: ThreadMail | null | undefined }) {
  if (!mail || mail.waiting === null) return null;
  return (
    <Alert color="yellow" py={6} px="sm">
      <Text span size="sm" fw={600}>
        Письма переписки ждут свой ящик
      </Text>{' '}
      —{' '}
      <Text span size="sm">
        {mail.waiting}
      </Text>
    </Alert>
  );
}
