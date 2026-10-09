/**
 * Ответ собеседнику из переписки (`POST /api/threads/{id}/answer`).
 */

import { Button, Group, Stack, Text, Textarea } from '@mantine/core';
import { useState } from 'react';

import type { FileRules, OutgoingFile } from '../api/files';
import { AnswerFiles } from './AnswerFiles';

interface AnswerProps {
  answered: boolean;
  busy: boolean;
  onSend: (text: string, fileIds: number[]) => void;
  /** Ответ лиду продаж: подпись и физический адрес дописывает сервер (модуль продаж). */
  signed?: boolean;
  /** Переписка — для скрепки: файл загружается к ней до отправки. */
  threadId: number;
  /** Файлы, загруженные к ответу и ещё не ушедшие, — с сервера. */
  pendingFiles?: OutgoingFile[];
  /** Правила файла — с сервера; нет — проверяет только сервер. */
  rules?: FileRules;
}

/** С какого ящика уйдёт ответ — у самого поля, а не карточкой внизу переписки.
 *  До 06.10.2026 там стояла отдельная карточка «ответ из карточки появится
 *  вместе с подключением почты» — и после того, как ответ заработал. */
const FROM_HINT =
  'Уйдёт сразу — с того же ящика, что вёл переписку, и веткой к этому письму: смена ' +
  'отправителя посреди разговора уводит письма в спам.';

/** Ответ лиду продаж: блок настроек «Отправителя» модуль продаж дописывает в конец сам —
 *  написанная в тексте подпись ушла бы второй. */
const SIGNED_HINT =
  ' Подпись и физический адрес из настроек «Отправителя» допишутся сами — в тексте их не нужно.';

/**
 * Ответ собеседнику — письмом из системы, а не из своей почты: тем ящиком,
 * что начал переписку, веткой к его письму. Уходит сразу по «Отправить».
 *
 * Стоит своей карточкой под лентой, отдельно от разбора цены: «Ответить»
 * вплотную под «Подтвердить» и «Не продаёт размещения» читалась третьей
 * кнопкой формы цены, а не отдельным действием. Отвечает на последний ответ
 * человека (`threadTimeline.answerTarget`).
 */
export function AnswerBox({
  answered,
  busy,
  onSend,
  signed = false,
  threadId,
  pendingFiles = [],
  rules,
}: AnswerProps) {
  // Загруженный, но не ушедший файл — ответ начат: форма открыта сразу.
  const [open, setOpen] = useState(pendingFiles.length > 0);
  const [text, setText] = useState('');
  const [files, setFiles] = useState<OutgoingFile[]>(pendingFiles);
  return (
    <>
      {answered ? (
        <Text size="sm" c="dimmed">
          Ответили — наше письмо в ленте выше.
        </Text>
      ) : open ? (
        <Stack gap="xs">
          <Textarea
            aria-label="Текст ответа"
            description={signed ? FROM_HINT + SIGNED_HINT : FROM_HINT}
            autosize
            minRows={4}
            value={text}
            onChange={(event) => setText(event.currentTarget.value)}
          />
          <AnswerFiles
            threadId={threadId}
            files={files}
            onChange={setFiles}
            {...(rules === undefined ? {} : { rules })}
            disabled={busy}
          />
          <Group>
            <Button
              color="lagoon"
              className="press"
              loading={busy}
              disabled={text.trim() === ''}
              onClick={() =>
                onSend(
                  text,
                  files.map((file) => file.id),
                )
              }
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
