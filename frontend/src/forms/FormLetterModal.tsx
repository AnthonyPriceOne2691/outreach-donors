/**
 * «Форма и письмо» — окно строки «Форм»: где форма донора и какой текст в неё вставить.
 *
 * До 20.10.2026 «Формы» давали ссылку на сайт и две кнопки исхода, а форму на сайте и
 * текст письма человек искал и сочинял сам. Теперь — прямая ссылка на страницу формы
 * (её нашла лестница) и текст, тот же, что ушёл бы донору письмом: шаблон рассылки
 * донорам, переписанный моделью под этот сайт (`letters/form_letter.py`). Тема и текст
 * копируются отдельно: у формы это обычно два поля.
 *
 * **Форму отправляет человек.** У форм капча и защита от спама; робот, который их
 * проходит, ведёт себя как спам-робот, и такой поток режут и форму, и домены рассылки.
 * Окно так и говорит — простыми словами.
 *
 * **Текст собирается задачей и хранится**: сборка — вызов модели, второй раз окно берёт
 * собранный. «Собрать заново» — по просьбе: прежний текст заменится новым.
 */

import {
  Alert,
  Anchor,
  Button,
  CopyButton,
  Group,
  Loader,
  Modal,
  Stack,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { buildFormLetter, fetchFormLetter } from '../api/deeper';
import type { FormLetterCard, FormLetterView } from '../api/deeper';
import { fetchJob } from '../api/jobs';
import type { JobState } from '../api/types';
import { PERMISSION_TITLES } from '../api/labels';
import { useSession } from '../auth/AuthProvider';
import { FIELD_GAP } from '../components/formRhythm';
import { formatDateTime } from '../format';
import { JobLine, jobRestarted } from '../jobs/JobLine';
import { notify } from '../notices';

const ACTIVE: ReadonlySet<JobState> = new Set(['queued', 'running', 'retry_wait']);

/** Как отправляется форма — одним абзацем внизу окна. */
const HOW =
  'Форму отправляете вы: сервис форм не заполняет — у них капча и защита от спама, ' +
  'а робот, который их обходит, выглядит как спам. В поле почты формы впишите ящик, ' +
  'который читаете; ответ придёт письмом — впишите адрес донора кнопкой «Вписать адрес», ' +
  'и ему уйдёт письмо обычным порядком.';

export interface LetterTarget {
  donorId: number;
  host: string;
}

function letterKey(donorId: number) {
  return ['form-letter', donorId] as const;
}

function FormLink({ view }: { view: FormLetterView }) {
  if (view.form_url !== null) {
    return (
      <Text size="sm">
        Форма:{' '}
        {/* Чернилами и подчёркнутой: акцент на плотном стекле окна дал 4,29 : 1 при
            норме 4,5 (замер 20.10.2026). */}
        <Anchor
          href={view.form_url}
          target="_blank"
          rel="noreferrer"
          size="sm"
          c="var(--ink)"
          underline="always"
        >
          открыть страницу формы
        </Anchor>
        .
      </Text>
    );
  }
  return (
    <Text size="sm">
      Где форма, не записано: сайт искали до 20.10.2026, когда адрес формы ещё не запоминался.{' '}
      <Anchor
        href={`https://${view.host}`}
        target="_blank"
        rel="noreferrer"
        size="sm"
        c="var(--ink)"
        underline="always"
      >
        Открыть сайт
      </Anchor>
      .
    </Text>
  );
}

function Copy({ value, label }: { value: string; label: string }) {
  return (
    <CopyButton value={value}>
      {({ copied, copy }) => (
        <Button variant="default" className="press" onClick={copy}>
          {copied ? 'Скопировано' : label}
        </Button>
      )}
    </CopyButton>
  );
}

function Letter({ letter }: { letter: FormLetterCard }) {
  return (
    <Stack gap={FIELD_GAP}>
      {letter.unset.length > 0 && (
        <Alert color="yellow" title="В тексте метка вместо значения">
          {letter.unset.map((title) => `«${title}»`).join(', ')} — настройка не заполнена. Такой
          текст в форму не вставляют: сначала её заполняет администратор, потом — «Собрать заново».
        </Alert>
      )}
      <TextInput label="Тема" value={letter.subject} readOnly />
      <Textarea label="Текст" value={letter.body} readOnly autosize minRows={8} />
      <Group gap="xs">
        <Copy value={letter.body} label="Скопировать текст" />
        <Copy value={letter.subject} label="Скопировать тему" />
      </Group>
      <Text size="sm" c="dimmed">
        Основа — {letter.based_on}; собран {formatDateTime(letter.built_at)}.
        {letter.notes ? ` Часть текста осталась шаблонной: ${letter.notes}.` : ''}
      </Text>
    </Stack>
  );
}

/** Текст письма окна: запрос, сборка задачей и идёт ли она. */
function useFormLetter(donorId: number) {
  const queryClient = useQueryClient();
  const [started, setStarted] = useState<string | null>(null);
  const view = useQuery({
    queryKey: letterKey(donorId),
    queryFn: () => fetchFormLetter(donorId),
  });
  const build = useMutation({
    mutationFn: (again: boolean) => buildFormLetter(donorId, again),
    onSuccess: async (answer) => {
      if (answer.job_id === null) {
        queryClient.setQueryData(letterKey(answer.donor_id), answer);
        return;
      }
      setStarted(answer.job_id);
      await jobRestarted(queryClient, answer.job_id);
    },
    onError: (failure) =>
      notify({ title: 'Текст не собирается', message: refusalOf(failure), color: 'red' }),
  });
  const jobId = started ?? view.data?.job_id ?? null;
  // Тот же запрос, что ведёт строка задачи: пока текст собирается, второй сборки нет.
  const job = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => fetchJob(jobId ?? ''),
    enabled: jobId !== null,
    retry: false,
  });
  const building = job.data !== undefined && ACTIVE.has(job.data.state);
  const reread = () => void queryClient.invalidateQueries({ queryKey: letterKey(donorId) });
  return { view, build, jobId, building, reread };
}

/** «Собрать текст» / «Собрать заново» — или почему собирать нельзя. */
function BuildButton({
  built,
  busy,
  onBuild,
}: {
  built: boolean;
  busy: boolean;
  onBuild: (again: boolean) => void;
}) {
  const { can } = useSession();
  if (!can('run')) {
    return (
      <Text size="sm" c="dimmed">
        Собирать текст может сотрудник с правом «{PERMISSION_TITLES.run}».
      </Text>
    );
  }
  return (
    <Group gap="xs">
      <Button
        variant={built ? 'subtle' : 'gradient'}
        className="press"
        loading={busy}
        onClick={() => onBuild(built)}
      >
        {built ? 'Собрать заново' : 'Собрать текст'}
      </Button>
    </Group>
  );
}

/** Содержимое окна одного донора: ссылка, текст, задача сборки, кнопка, как отправлять. */
function LetterWindow({ donorId }: { donorId: number }) {
  const { view, build, jobId, building, reread } = useFormLetter(donorId);
  if (view.error) {
    return (
      <Alert color="red" title="Окно не загрузилось">
        {refusalOf(view.error)}
      </Alert>
    );
  }
  if (view.data === undefined) return <Loader aria-label="Загружаем форму и письмо" />;
  const { letter } = view.data;
  return (
    <Stack gap={FIELD_GAP}>
      <FormLink view={view.data} />
      {letter !== null ? <Letter letter={letter} /> : <Text size="sm">{NOT_BUILT}</Text>}
      {jobId !== null && <JobLine jobId={jobId} onFinished={reread} />}
      <BuildButton
        built={letter !== null}
        busy={build.isPending || building}
        onBuild={(again) => build.mutate(again)}
      />
      <Text size="sm" c="dimmed">
        {HOW}
      </Text>
    </Stack>
  );
}

/** Что сказать, пока текста нет. */
const NOT_BUILT =
  'Текст ещё не собран. Он тот же, что ушёл бы донору письмом: текст рассылки донорам, ' +
  'переписанный моделью под этот сайт. Сборка — вызов модели, около минуты.';

export function FormLetterModal({
  target,
  onClose,
}: {
  target: LetterTarget | null;
  onClose: () => void;
}) {
  // Окно другого донора — своё содержимое с чистого листа: ключ — номер донора.
  return (
    <Modal
      opened={target !== null}
      onClose={onClose}
      title={`Форма и письмо: ${target?.host ?? ''}`}
      size={780}
    >
      {target !== null && <LetterWindow key={target.donorId} donorId={target.donorId} />}
    </Modal>
  );
}
