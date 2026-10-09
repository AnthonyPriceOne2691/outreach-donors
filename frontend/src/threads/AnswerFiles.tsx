/**
 * Скрепка в ответе: файл уходит на сервер сразу, письмо — по «Отправить».
 *
 * **Правила файла называет сервер** (`file_rules`): тип, размер файла и письма,
 * число файлов. Здесь они проверяются до загрузки — иначе файл больше потолка
 * тела запроса отбивал бы прокси страницей без слов, — а сервер проверяет
 * всё ещё раз, по содержимому, и отказывает словами.
 *
 * **Загруженный файл ждёт в переписке**, пока ответ не уйдёт: после
 * перезагрузки страницы скрепка восстанавливает его (`pending_files`).
 */

import { Badge, Button, CloseButton, FileButton, Group, Stack, Text } from '@mantine/core';
import { IconPaperclip } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import type { FileRules, OutgoingFile } from '../api/files';
import { attachFile, detachFile } from '../api/outreach';
import { formatBytes } from '../format';

/** Мегабайты — десятичные, как считает сервер. */
function megabytes(bytes: number): string {
  return `${Math.round((bytes / 1_000_000) * 10) / 10} МБ`;
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot < 0 ? '' : name.slice(dot + 1).toLowerCase();
}

/** Почему файл не приложить — словами, до загрузки; `null` — можно. */
export function refusalBefore(
  file: File,
  attached: OutgoingFile[],
  rules: FileRules,
): string | null {
  if (!rules.extensions.includes(extensionOf(file.name))) {
    const allowed = rules.extensions.map((ext) => ext.toUpperCase()).join(', ');
    return `«${file.name}»: такие файлы с письмом не уходят — можно ${allowed}`;
  }
  if (file.size > rules.max_file_bytes) {
    return `«${file.name}» больше предела ${megabytes(rules.max_file_bytes)} на файл — уменьшите его или дайте ссылку в тексте ответа`;
  }
  if (attached.length >= rules.max_files) {
    return `К письму — не больше ${rules.max_files} файлов: уберите лишние`;
  }
  const total = attached.reduce((sum, one) => sum + one.size, file.size);
  if (total > rules.max_letter_bytes) {
    return `Файлы письма вместе больше предела ${megabytes(rules.max_letter_bytes)} на письмо — уберите лишние или дайте ссылку в тексте ответа`;
  }
  return null;
}

interface Props {
  threadId: number;
  files: OutgoingFile[];
  onChange: (files: OutgoingFile[]) => void;
  rules?: FileRules;
  disabled: boolean;
}

export function AnswerFiles({ threadId, files, onChange, rules, disabled }: Props) {
  const [refusal, setRefusal] = useState<string | null>(null);
  const attach = useMutation({
    mutationFn: (file: File) => attachFile(threadId, file),
    onSuccess: (got) => onChange([...files, got]),
    onError: (failure) => setRefusal(refusalOf(failure)),
  });
  const detach = useMutation({
    mutationFn: (file: OutgoingFile) => detachFile(threadId, file.id),
    onSuccess: (_, file) => onChange(files.filter((one) => one.id !== file.id)),
    onError: (failure) => setRefusal(refusalOf(failure)),
  });
  const pick = (file: File | null) => {
    if (file === null) return;
    const why = rules === undefined ? null : refusalBefore(file, files, rules);
    setRefusal(why);
    if (why === null) attach.mutate(file);
  };
  const accept =
    rules === undefined ? {} : { accept: rules.extensions.map((ext) => `.${ext}`).join(',') };
  return (
    <Stack gap={6}>
      <Group gap="xs" wrap="wrap">
        <FileButton onChange={pick} {...accept} disabled={disabled || attach.isPending}>
          {(props) => (
            <Button
              {...props}
              variant="default"
              size="compact-sm"
              leftSection={<IconPaperclip size={14} aria-hidden />}
              loading={attach.isPending}
            >
              Приложить файл
            </Button>
          )}
        </FileButton>
        {files.map((file) => (
          <Badge
            key={file.id}
            variant="light"
            color="gray"
            className="answerFile"
            rightSection={
              <CloseButton
                size="xs"
                aria-label={`Убрать ${file.name}`}
                disabled={disabled || detach.isPending}
                onClick={() => detach.mutate(file)}
              />
            }
          >
            {file.name} · {formatBytes(file.size)}
          </Badge>
        ))}
      </Group>
      {refusal !== null ? (
        <Text size="sm" c="red" role="alert">
          {refusal}
        </Text>
      ) : null}
    </Stack>
  );
}
