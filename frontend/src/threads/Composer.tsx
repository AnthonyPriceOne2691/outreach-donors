/**
 * Ответ собеседнику — полем внизу ленты, как в мессенджере
 * (`POST /api/threads/{id}/answer`).
 *
 * Замечание Anthony 10.10.2026: «кнопку „Ответить“ перенести, поле для набора
 * текста — туда, где отображаются сообщения, туда же скрепку». До этого ответ
 * открывался кнопкой «Ответить» в отдельной карточке под лентой и разбором цены:
 * лишнее нажатие на каждый ответ, а поле появлялось в другом месте экрана, чем
 * переписка, на которую отвечают.
 *
 * **Поле видно сразу**, скрепка слева, «Отправить» справа; Ctrl+Enter (⌘+Enter)
 * отправляет — Enter переносит строку: ответ донору — письмо, а не реплика чата.
 * Что ответ уйдёт с ящика переписки веткой к письму (и у лида продаж — что подпись
 * допишется сама), — описание поля: его читает диктор, а глазу — подсказка в поле
 * и «i» рядом.
 *
 * Отвечает на последний ответ человека (`threadTimeline.answerTarget`). Ответили —
 * вместо поля строка «Ответили — наше письмо в ленте выше»: второй ответ на то же
 * письмо сервер не примет, поле откроется снова со следующим ответом собеседника.
 * Ящика переписки больше нет (`boxGone`) — вместо поля причина: ответ с другого ящика
 * не уходит, а до 10.10.2026 об этом говорил только отказ после Ctrl+Enter.
 *
 * **Приложенные файлы — в кэше переписки, а не своей копией** (проверка QA
 * 10.10.2026). Копия снималась с ответа сервера, когда поле рисовалось: ушёл в
 * соседний диалог и вернулся — поле рисовалось заново по кэшу, снятому до
 * загрузки, и значки файлов пропадали, хотя файлы лежали на сервере. Ответ ушёл
 * бы без них, а повторная скрепка дала бы дубли. Загрузка и снятие правят кэш
 * переписки сразу и переспрашивают сервер: что ждёт письма, решает он.
 */

import {
  ActionIcon,
  Badge,
  CloseButton,
  FileButton,
  Group,
  Stack,
  Text,
  Textarea,
  Tooltip,
} from '@mantine/core';
import { IconPaperclip, IconSend } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import type { KeyboardEvent } from 'react';

import { refusalOf } from '../api/client';
import type { FileRules, OutgoingFile } from '../api/files';
import { attachFile, detachFile } from '../api/outreach';
import type { ThreadView } from '../api/thread';
import { InfoHint } from '../components/InfoHint';
import { formatBytes, plural } from '../format';
import { refusalBefore } from './fileRules';

/** С какого ящика уйдёт ответ: смена отправителя посреди разговора уводит в спам. */
export const FROM_HINT =
  'Уйдёт сразу — с того же ящика, что вёл переписку, и веткой к этому письму: смена ' +
  'отправителя посреди разговора уводит письма в спам.';

/** Ответ лиду продаж: блок настроек «Отправителя» модуль продаж дописывает в конец сам —
 *  написанная в тексте подпись ушла бы второй. */
export const SIGNED_HINT =
  ' Подпись и физический адрес из настроек «Отправителя» допишутся сами — в тексте их не нужно.';

/** Ящика переписки нет — ответ не уйдёт ни с него, ни с другого (`letters/mailbox.py`). */
const BOX_GONE =
  'Ответ отсюда не уйдёт: ящик, которым начата переписка, удалён, а с другого ящика ' +
  'письмо переписки не отправляется.';

/** Брошенный файл убирает сервер (процесс добивок): человек знает срок заранее. */
function pendingNote(days: number): string {
  return `Не ушедший с письмом за ${days} ${plural(days, 'день', 'дня', 'дней')} файл уберётся сам`;
}

interface Props {
  answered: boolean;
  busy: boolean;
  onSend: (text: string, fileIds: number[]) => void;
  /** Ответ лиду продаж: подпись и физический адрес дописывает сервер (модуль продаж). */
  signed?: boolean;
  /** Переписка — для скрепки: файл загружается к ней до отправки. */
  threadId: number;
  /** Файлы, загруженные к ответу и ещё не ушедшие, — с сервера, из кэша переписки. */
  pendingFiles?: OutgoingFile[];
  /** Правила файла — с сервера; нет — проверяет только сервер. */
  rules?: FileRules;
  /** Ящика переписки больше нет (`ThreadMailLine.mailboxGone`): вместо поля — почему. */
  boxGone?: boolean;
}

/** Ctrl+Enter или ⌘+Enter — отправить; просто Enter — новая строка. */
function sendsOn(event: KeyboardEvent): boolean {
  return event.key === 'Enter' && (event.ctrlKey || event.metaKey);
}

/**
 * Набранный ответ — черновиком на переписку в этой вкладке (`sessionStorage`).
 * Поле видно всегда, и уйти из переписки посреди ответа легко — J/K, «Следующий
 * ждущий», строка списка: прежде набранное молча пропадало (попутный аудит
 * 10.10.2026). Ответ ушёл — черновик стирается. Хранилище закрыто — без черновика.
 */
function useDraft(threadId: number, answered: boolean): [string, (text: string) => void] {
  const key = `outreach.answer.${threadId}`;
  const [text, setText] = useState(() => {
    try {
      return sessionStorage.getItem(key) ?? '';
    } catch {
      return '';
    }
  });
  const keep = (next: string) => {
    setText(next);
    try {
      if (next === '') sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, next);
    } catch {
      // Хранилище закрыто — набранное живёт, пока открыта переписка.
    }
  };
  useEffect(() => {
    if (!answered) return;
    try {
      sessionStorage.removeItem(key);
    } catch {
      // Нечего стирать.
    }
  }, [answered, key]);
  return [text, keep];
}

/** Ключ кэша переписки — тот же, что у экрана (`ThreadPage`). */
function threadKey(threadId: number): string[] {
  return ['thread', String(threadId)];
}

/** Скрепка и крестик: файлы — те, что пришли с перепиской (`files`), правка — в её кэш. */
function useAttachments(threadId: number, files: OutgoingFile[], rules: FileRules | undefined) {
  const queryClient = useQueryClient();
  const [refusal, setRefusal] = useState<string | null>(null);
  // Сразу — в кэш, чтобы значок встал без ожидания; следом — заново с сервера: так
  // и чтение переписки, начатое до загрузки, не вернёт список без нового файла.
  const settle = (change: (was: OutgoingFile[]) => OutgoingFile[]) => {
    queryClient.setQueryData<ThreadView>(threadKey(threadId), (view) =>
      view === undefined ? view : { ...view, pending_files: change(view.pending_files ?? []) },
    );
    void queryClient.invalidateQueries({ queryKey: threadKey(threadId) });
  };
  const attach = useMutation({
    mutationFn: (file: File) => attachFile(threadId, file),
    onSuccess: (got) => settle((was) => [...was, got]),
    onError: (failure) => setRefusal(refusalOf(failure)),
  });
  const detach = useMutation({
    mutationFn: (file: OutgoingFile) => detachFile(threadId, file.id),
    onSuccess: (_, file) => settle((was) => was.filter((one) => one.id !== file.id)),
    onError: (failure) => setRefusal(refusalOf(failure)),
  });
  const pick = (file: File | null) => {
    if (file === null) return;
    const why = rules === undefined ? null : refusalBefore(file, files, rules);
    setRefusal(why);
    if (why === null) attach.mutate(file);
  };
  return { files, refusal, attach, detach, pick };
}

/** Слова поля: у лида продаж — что подпись допишется сама. */
const WORDS = {
  plain: { hint: FROM_HINT, placeholder: 'Ответ собеседнику' },
  signed: { hint: FROM_HINT + SIGNED_HINT, placeholder: 'Ответ лиду — подпись допишется сама' },
} as const;

type Attachments = ReturnType<typeof useAttachments>;

/** Приложенные файлы над полем — с крестиком; и срок, после которого брошенный уберётся. */
function AttachedFiles({
  box,
  busy,
  rules,
}: {
  box: Attachments;
  busy: boolean;
  rules?: FileRules;
}) {
  if (box.files.length === 0) return null;
  return (
    <Group gap="xs" wrap="wrap">
      {box.files.map((file) => (
        <Badge
          key={file.id}
          variant="light"
          color="gray"
          className="answerFile"
          rightSection={
            <CloseButton
              size="xs"
              aria-label={`Убрать ${file.name}`}
              disabled={busy || box.detach.isPending}
              onClick={() => box.detach.mutate(file)}
            />
          }
        >
          {file.name} · {formatBytes(file.size)}
        </Badge>
      ))}
      {rules?.pending_days !== undefined ? (
        <Text size="xs" c="dimmed">
          {pendingNote(rules.pending_days)}
        </Text>
      ) : null}
    </Group>
  );
}

/** Скрепка: файл уходит на сервер сразу, письмо — по «Отправить». */
function AttachButton({
  box,
  busy,
  rules,
}: {
  box: Attachments;
  busy: boolean;
  rules?: FileRules;
}) {
  const accept =
    rules === undefined ? {} : { accept: rules.extensions.map((ext) => `.${ext}`).join(',') };
  return (
    <FileButton onChange={box.pick} {...accept} disabled={busy || box.attach.isPending}>
      {(props) => (
        <Tooltip label="Приложить файл" withArrow>
          <ActionIcon
            {...props}
            variant="subtle"
            size="lg"
            radius="xl"
            aria-label="Приложить файл"
            loading={box.attach.isPending}
          >
            <IconPaperclip size={20} />
          </ActionIcon>
        </Tooltip>
      )}
    </FileButton>
  );
}

/** Поле открыто: что набрано, файлы и как отправить. */
interface FieldProps extends Pick<Props, 'busy' | 'onSend' | 'signed' | 'rules'> {
  text: string;
  setText: (text: string) => void;
  box: Attachments;
}

function AnswerField({ text, setText, box, busy, onSend, signed = false, rules }: FieldProps) {
  const words = signed ? WORDS.signed : WORDS.plain;
  const ruled = rules === undefined ? {} : { rules };
  const ready = text.trim() !== '' && !busy && !box.attach.isPending;
  const send = () => {
    if (ready)
      onSend(
        text,
        box.files.map((file) => file.id),
      );
  };
  return (
    <Stack gap={6} className="composer">
      <AttachedFiles box={box} busy={busy} {...ruled} />
      <Group gap="xs" wrap="nowrap" align="flex-end">
        <AttachButton box={box} busy={busy} {...ruled} />
        <Textarea
          aria-label="Текст ответа"
          placeholder={words.placeholder}
          description={words.hint}
          classNames={{ description: 'srOnly' }}
          autosize
          minRows={1}
          maxRows={8}
          radius="lg"
          style={{ flex: 1 }}
          value={text}
          disabled={busy}
          onChange={(event) => setText(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (!sendsOn(event)) return;
            event.preventDefault();
            send();
          }}
        />
        <InfoHint name="Как уйдёт ответ" width={320}>
          {words.hint}
        </InfoHint>
        <Tooltip label="Отправить · Ctrl+Enter" withArrow>
          <ActionIcon
            color="lagoon"
            size="lg"
            radius="xl"
            className="press"
            aria-label="Отправить"
            loading={busy}
            disabled={!ready}
            onClick={send}
          >
            <IconSend size={18} />
          </ActionIcon>
        </Tooltip>
      </Group>
      {box.refusal !== null ? (
        <Text size="sm" c="red" role="alert">
          {box.refusal}
        </Text>
      ) : null}
    </Stack>
  );
}

export function Composer({ answered, threadId, pendingFiles, boxGone, ...field }: Props) {
  // Черновик и файлы — до решения, открыто ли поле: ушедший ответ стирает черновик.
  const [text, setText] = useDraft(threadId, answered);
  const box = useAttachments(threadId, pendingFiles ?? [], field.rules);
  const shut = answered ? 'Ответили — наше письмо в ленте выше.' : boxGone ? BOX_GONE : null;
  if (shut !== null) {
    return (
      <Text size="sm" c="dimmed" className="composer">
        {shut}
      </Text>
    );
  }
  return <AnswerField {...field} text={text} setText={setText} box={box} />;
}
