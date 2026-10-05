/**
 * Ответ собеседнику из переписки (`POST /api/threads/{id}/answer`).
 */

import { Button, Group, Stack, Text, Textarea } from '@mantine/core';
import { useState } from 'react';

interface AnswerProps {
  answered: boolean;
  busy: boolean;
  onSend: (text: string) => void;
}

/**
 * Ответ собеседнику — письмом из системы, а не из своей почты: тем ящиком,
 * что начал переписку, веткой к его письму. Уходит сразу по «Отправить».
 */
export function AnswerBox({ answered, busy, onSend }: AnswerProps) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  if (answered) {
    return (
      <Text size="sm" c="dimmed" mt="sm">
        Ответили — письмо ниже в переписке.
      </Text>
    );
  }
  if (!open) {
    return (
      <Group mt="sm">
        <Button variant="light" size="xs" onClick={() => setOpen(true)}>
          Ответить
        </Button>
      </Group>
    );
  }
  return (
    <Stack gap="xs" mt="sm">
      <Textarea
        aria-label="Текст ответа"
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
  );
}
