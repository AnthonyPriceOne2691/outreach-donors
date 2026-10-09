/**
 * Файлы нашего ответа — значками в пузыре письма, со скачиванием.
 *
 * Скачивание — запросом с пропуском, а не ссылкой (как у вложений ответа):
 * простая ссылка заголовка `Authorization` не несёт.
 */

import { Button, Group } from '@mantine/core';
import { IconPaperclip } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';

import { refusalOf } from '../api/client';
import { saveFile } from '../api/donors';
import type { OutgoingFile } from '../api/files';
import { downloadLetterFile } from '../api/outreach';
import { formatBytes } from '../format';
import { notify } from '../notices';

export function LetterFiles({
  messageId,
  files,
}: {
  messageId: number;
  files: OutgoingFile[] | undefined;
}) {
  const save = useMutation({
    mutationFn: (file: OutgoingFile) => downloadLetterFile(messageId, file.id),
    onSuccess: (got, file) => saveFile(got, file.name),
    onError: (failure) =>
      notify({ title: 'Файл не скачался', message: refusalOf(failure), color: 'red' }),
  });
  if (!files?.length) return null;
  return (
    <Group
      gap={6}
      mt={8}
      wrap="wrap"
      component="ul"
      className="letterFiles"
      aria-label="Файлы письма"
    >
      {files.map((file) => (
        <li key={file.id}>
          <Button
            variant="default"
            size="compact-xs"
            leftSection={<IconPaperclip size={14} aria-hidden />}
            loading={save.isPending && save.variables?.id === file.id}
            onClick={() => save.mutate(file)}
            aria-label={`Скачать ${file.name}`}
          >
            {file.name} · {formatBytes(file.size)}
          </Button>
        </li>
      ))}
    </Group>
  );
}
