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
 */

import { Alert, Text } from '@mantine/core';

import type { ThreadMail } from '../api/thread';
import { formatDateTime } from '../format';

export function ThreadMailLine({ mail }: { mail: ThreadMail | null | undefined }) {
  if (!mail) return null;
  const who = mail.mailbox === null ? 'Ящик переписки удалён' : `Пишет ${mail.mailbox}`;
  const next =
    mail.next_step !== null && mail.next_at !== null
      ? ` · добивка ${mail.next_step} — ${formatDateTime(mail.next_at)}`
      : '';
  return (
    <>
      <Text size="sm" c="dimmed">
        {who}
        {next}
      </Text>
      {mail.waiting !== null && (
        <Alert color="yellow" title="Письма переписки ждут свой ящик" mt="xs">
          {mail.waiting}
        </Alert>
      )}
    </>
  );
}
