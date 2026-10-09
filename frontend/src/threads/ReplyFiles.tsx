/**
 * Вложения ответа: имя, размер и «Скачать» у сохранённых.
 *
 * Прайс приходит файлом чаще, чем текстом, и другой копии письма нет —
 * платформа приёма единственный его получатель. Поэтому файл, который
 * сервер сохранил, открывается отсюда, а у несохранённого (опасный, лишний,
 * слишком большой) причина видна сразу, без раскрытия: по ней человек решает,
 * просить ли донора прислать прайс ещё раз.
 *
 * **Скачивание — запросом с пропуском, а не ссылкой** (тот же помощник, что
 * у выгрузки доноров): простая ссылка заголовка `Authorization` не несёт.
 * Сервер отдаёт файл только на скачивание — показывать присланное донором
 * внутри страницы нельзя: HTML из вложения выполнился бы с нашего адреса.
 *
 * **Текст файла — можно** (с 09.10.2026): сервер читает прайс из PDF, Excel,
 * Word и CSV для разбора цены, и «Текст» открывает ровно то, что видела модель, —
 * простым текстом, без разметки файла. Почему текста нет или он неполный —
 * словами сервера под именем файла.
 */

import { Alert, Badge, Button, Divider, Group, Loader, Modal, Stack, Text } from '@mantine/core';
import { IconPaperclip } from '@tabler/icons-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { saveFile } from '../api/donors';
import type { ReplyFile } from '../api/files';
import { downloadAttachment, fetchAttachmentText } from '../api/outreach';
import { formatBytes, formatNumber } from '../format';
import { notify } from '../notices';

interface Props {
  replyId: number;
  files: ReplyFile[];
}

/** Текст открыть можно у сохранённого файла, если он прочитан или ещё не читался
 *  (сервер прочитает при запросе). Пометка без текста — читать нечего. */
function readable(file: ReplyFile): boolean {
  return file.accepted && (file.has_text === true || !file.text_note);
}

/** Текст файла — тот, что видела модель разбора цены, простым текстом. */
function FileTextModal({
  replyId,
  file,
  onClose,
}: {
  replyId: number;
  file: ReplyFile;
  onClose: () => void;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['attachment-text', replyId, file.id],
    queryFn: () => fetchAttachmentText(replyId, file.id),
  });
  return (
    <Modal opened onClose={onClose} size="xl" title={`Текст файла «${file.name}»`}>
      {isLoading ? <Loader aria-label="Читаем файл" /> : null}
      {error ? (
        <Alert color="red" title="Текст не открылся">
          {refusalOf(error)}
        </Alert>
      ) : null}
      {data?.note ? (
        <Text size="sm" c="dimmed" mb="sm">
          {data.note}
        </Text>
      ) : null}
      {data?.text ? (
        <Text component="pre" size="sm" className="fileText">
          {data.text}
        </Text>
      ) : null}
    </Modal>
  );
}

export function ReplyFiles({ replyId, files }: Props) {
  const [shown, setShown] = useState<ReplyFile | null>(null);
  const save = useMutation({
    mutationFn: (file: ReplyFile) => downloadAttachment(replyId, file.id),
    onSuccess: (got, file) => saveFile(got, file.name),
    onError: (failure) =>
      notify({ title: 'Файл не скачался', message: refusalOf(failure), color: 'red' }),
  });

  if (files.length === 0) return null;

  return (
    <>
      <Divider my="sm" variant="dashed" />
      <Text size="xs" c="dimmed" mb={6}>
        Вложения · {formatNumber(files.length)}
      </Text>
      <Stack gap={8} component="ul" className="replyFiles" aria-label="Вложения">
        {files.map((file) => (
          <Group
            key={file.id}
            component="li"
            justify="space-between"
            wrap="nowrap"
            gap="sm"
            align="flex-start"
          >
            <Group gap={8} wrap="nowrap" align="flex-start" className="replyFileName">
              <IconPaperclip size={16} aria-hidden className="replyFileIcon" />
              <div>
                <Text size="sm" className="replyFileTitle">
                  {file.name}
                </Text>
                <Text size="xs" c="dimmed">
                  {formatBytes(file.size)}
                  {file.accepted || file.reason === null ? '' : ` · ${file.reason}`}
                </Text>
                {file.accepted && file.text_note ? (
                  <Text size="xs" c="dimmed">
                    {file.text_note}
                  </Text>
                ) : null}
              </div>
            </Group>
            {file.accepted ? (
              <Group gap={6} wrap="nowrap" className="replyFileMark">
                {readable(file) ? (
                  <Button
                    variant="default"
                    size="compact-sm"
                    onClick={() => setShown(file)}
                    aria-label={`Текст ${file.name}`}
                  >
                    Текст
                  </Button>
                ) : null}
                <Button
                  variant="default"
                  size="compact-sm"
                  className="press"
                  loading={save.isPending && save.variables?.id === file.id}
                  onClick={() => save.mutate(file)}
                  aria-label={`Скачать ${file.name}`}
                >
                  Скачать
                </Button>
              </Group>
            ) : (
              <Badge variant="light" color="red" className="replyFileMark">
                не сохранён
              </Badge>
            )}
          </Group>
        ))}
      </Stack>
      {shown !== null ? (
        <FileTextModal replyId={replyId} file={shown} onClose={() => setShown(null)} />
      ) : null}
    </>
  );
}
