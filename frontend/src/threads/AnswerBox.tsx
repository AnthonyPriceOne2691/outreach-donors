/**
 * Ответ собеседнику из переписки (`POST /api/threads/{id}/answer`).
 */

import { Button, Divider, Group, Stack, Text, Textarea } from '@mantine/core';
import { useState } from 'react';

interface AnswerProps {
  answered: boolean;
  busy: boolean;
  onSend: (text: string) => void;
}

/** С какого ящика уйдёт ответ — у самого поля, а не карточкой внизу переписки.
 *  До 06.10.2026 там стояла отдельная карточка «ответ из карточки появится
 *  вместе с подключением почты» — и после того, как ответ заработал. */
const FROM_HINT =
  'Уйдёт сразу — с того же ящика, что вёл переписку, и веткой к этому письму: смена ' +
  'отправителя посреди разговора уводит письма в спам.';

/**
 * Ответ собеседнику — письмом из системы, а не из своей почты: тем ящиком,
 * что начал переписку, веткой к его письму. Уходит сразу по «Отправить».
 *
 * Отделён от решения по цене штриховой линией: «Ответить» вплотную под
 * «Подтвердить» и «Не продаёт размещения» читалась третьей кнопкой формы
 * цены, а не отдельным действием.
 */
export function AnswerBox({ answered, busy, onSend }: AnswerProps) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  return (
    <>
      <Divider my="sm" variant="dashed" />
      {answered ? (
        <Text size="sm" c="dimmed">
          Ответили — письмо ниже в переписке.
        </Text>
      ) : open ? (
        <Stack gap="xs">
          <Textarea
            aria-label="Текст ответа"
            description={FROM_HINT}
            autosize
            minRows={4}
            value={text}
            onChange={(event) => setText(event.currentTarget.value)}
          />
          <Group>
            <Button
              color="lagoon"
              className="press"
              loading={busy}
              disabled={text.trim() === ''}
              onClick={() => onSend(text)}
            >
              Отправить
            </Button>
            <Button variant="subtle" disabled={busy} onClick={() => setOpen(false)}>
              Отмена
            </Button>
          </Group>
        </Stack>
      ) : (
        <Group>
          <Button variant="light" size="xs" onClick={() => setOpen(true)}>
            Ответить
          </Button>
        </Group>
      )}
    </>
  );
}
