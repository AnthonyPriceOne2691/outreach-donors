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
 */

import { Badge, Button, Divider, Group, Stack, Text } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { IconPaperclip } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { saveFile } from '../api/donors';
import { downloadAttachment } from '../api/outreach';
import type { ReplyAttachment } from '../api/types';
import { formatBytes, formatNumber } from '../format';

interface Props {
  replyId: number;
  files: ReplyAttachment[];
}

export function ReplyFiles({ replyId, files }: Props) {
  const save = useMutation({
    mutationFn: (file: ReplyAttachment) => downloadAttachment(replyId, file.id),
    onSuccess: (got, file) => saveFile(got, file.name),
    onError: (failure) =>
      notifications.show({ title: 'Файл не скачался', message: refusalOf(failure), color: 'red' }),
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
              </div>
            </Group>
            {file.accepted ? (
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
            ) : (
              <Badge variant="light" color="red" className="replyFileMark">
                не сохранён
              </Badge>
            )}
          </Group>
        ))}
      </Stack>
    </>
  );
}
