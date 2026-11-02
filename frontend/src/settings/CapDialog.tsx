/**
 * Окно смены лимита на «Расходе» — одна основа у капа Ahrefs, потолков модели и потолка проверок.
 *
 * Общий каркас, а не три копии: заголовок, пояснение, отказ сервера над полями, поля, «Отмена» и
 * «Сохранить». **Отказ сервера — над полем, вписанное остаётся** (правило форм, проверка QA
 * 10.10.2026): уведомление в углу закрывало бы кнопки окна. Почему «Сохранить» заперта — строкой
 * под кнопками (`SaveActions`).
 *
 * Здесь же — «кто и когда менял» и последние смены: одни слова у всех лимитов.
 */

import { Alert, Modal, Stack, Text } from '@mantine/core';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { refusalOf } from '../api/client';
import { FIELD_GAP } from '../components/formRhythm';
import { SaveActions } from '../components/SaveActions';
import { formatDateTime } from '../format';
import { notify } from '../notices';

/** Кто и когда сменил лимит: «кап@site.com · 16.10.2026, 14:05»; учётку удалили — только когда. */
export function changedBy(change: { set_by: string | null; set_at: string }): string {
  const when = formatDateTime(change.set_at);
  return change.set_by === null ? when : `${change.set_by} · ${when}`;
}

interface Saving<Body, Change> {
  /** Запрос смены. */
  save: (body: Body) => Promise<Change>;
  /** Что сказать после смены — словами того, что поставили. */
  words: (change: Change) => string;
  onClose: () => void;
  /** Кто ещё читает этот лимит, кроме «Расхода». */
  readers?: string[];
}

/** Смена лимита: после ответа сервера экраны перечитывают его, человеку — что поставили, окно
 *  закрывается. Отказ остаётся в окне (`error` у `CapDialog`). */
export function useCapSave<Body, Change>({
  save,
  words,
  onClose,
  readers = [],
}: Saving<Body, Change>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: save,
    onSuccess: async (change) => {
      await Promise.all(
        ['usage', ...readers].map((key) => queryClient.invalidateQueries({ queryKey: [key] })),
      );
      notify({ message: words(change), color: 'green' });
      onClose();
    },
  });
}

interface Props {
  title: string;
  /** Что держит этот лимит и когда он вступает в силу: над полями. */
  intro: ReactNode;
  onClose: () => void;
  /** Сохранить. */
  onSave: () => void;
  /** Отказ сервера — и заголовок отказа: «Кап не сменили». */
  error: Error | null;
  errorTitle: string;
  saving: boolean;
  /** Почему «Сохранить» заперта — словами; `null` — открыта. */
  locked: string | null;
  /** Заперта без строки: причина уже названа у поля. */
  disabled?: boolean;
  children: ReactNode;
}

export function CapDialog({
  title,
  intro,
  onClose,
  onSave,
  error,
  errorTitle,
  saving,
  locked,
  disabled = false,
  children,
}: Props) {
  return (
    <Modal opened onClose={onClose} title={title}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          // Запертую «Сохранить» Enter не нажмёт: браузер такую форму не отправляет. А кнопку с
          // идущим запросом он отправит — второй запрос держит это условие.
          if (!saving) onSave();
        }}
      >
        <Stack gap={FIELD_GAP}>
          {intro}
          {error ? (
            <Alert color="red" title={errorTitle}>
              {refusalOf(error)}
            </Alert>
          ) : null}
          {children}
          <SaveActions onCancel={onClose} saving={saving} disabled={disabled} locked={locked} />
        </Stack>
      </form>
    </Modal>
  );
}

/** Смена одной строкой: что поставили, кто и когда. */
interface Change {
  id: number;
  what: string;
  set_by: string | null;
  set_at: string;
}

/** Последние смены — что ставили, кто и когда: решая, видно, откуда пришло нынешнее. */
export function RecentChanges({ changes }: { changes: Change[] }) {
  return (
    <Stack gap={4}>
      <Text size="sm" fw={500}>
        Последние смены
      </Text>
      {changes.map((change) => (
        <Text key={change.id} size="sm" c="dimmed" style={{ overflowWrap: 'anywhere' }}>
          <b>{change.what}</b> — {changedBy(change)}
        </Text>
      ))}
    </Stack>
  );
}
