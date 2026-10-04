/**
 * Окно записи базы знаний: заведение новой и правка существующей.
 *
 * **Отказ — до нажатия, где он ясен заранее, и словами сервера, где нет.**
 * Пустое и слишком длинное поле видно сразу, границами сервера (`kbDraft.ts`);
 * код языка, занятый заголовок и прочее судит сервер — его отказ встаёт над
 * формой целиком, а введённое остаётся на месте.
 *
 * **Включение — в той же форме**: новая запись может сразу лечь выключенной —
 * так её готовят, не показывая агенту. В таблице включают переключателем строки.
 */

import {
  Alert,
  Button,
  Group,
  Modal,
  Select,
  Stack,
  Switch,
  TagsInput,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { addKbEntry, changeKbEntry } from '../api/sales';
import { KB_KINDS, kbKindTitle } from '../api/salesLabels';
import type { KbEntryCard, KbKind, KbLimits } from '../api/salesTypes';
import { formatNumber } from '../format';
import { changed, draftOf, refusalsOf } from './kbDraft';
import type { KbDraft } from './kbDraft';

interface Props {
  /** Правка — запись; новая — `null`. */
  entry: KbEntryCard | null;
  kinds: KbKind[];
  limits: KbLimits;
  onClose: () => void;
  onSaved: (saved: KbEntryCard) => void;
}

function kindOptions(kinds: KbKind[]) {
  return kinds.map((kind) => ({
    value: kind,
    label:
      kind in KB_KINDS ? `${KB_KINDS[kind].title} — ${KB_KINDS[kind].hint}` : kbKindTitle(kind),
  }));
}

/** Поля записи. Ошибки — уже посчитанные: форма их только показывает. */
function KbFields({
  draft,
  kinds,
  limits,
  refusals,
  onChange,
}: {
  draft: KbDraft;
  kinds: KbKind[];
  limits: KbLimits;
  refusals: ReturnType<typeof refusalsOf>;
  onChange: (patch: Partial<KbDraft>) => void;
}) {
  return (
    <>
      <Select
        label="Вид"
        description="по виду агент берёт факт под ход: цены — на вопрос о цене"
        allowDeselect={false}
        data={kindOptions(kinds)}
        value={draft.kind}
        onChange={(value) => value !== null && onChange({ kind: value as KbKind })}
      />
      <TextInput
        label="Язык"
        description="код языка письма: ru, en, pt-br"
        value={draft.language}
        error={refusals.language}
        onChange={(event) => onChange({ language: event.currentTarget.value })}
      />
      <TextInput
        label="Заголовок"
        description="по виду, языку и заголовку запись узнаёт и повторная загрузка файла"
        value={draft.title}
        error={refusals.title}
        onChange={(event) => onChange({ title: event.currentTarget.value })}
      />
      <Textarea
        label="Текст"
        description={`то, что прочтёт агент; до ${formatNumber(limits.text)} знаков`}
        autosize
        minRows={4}
        maxRows={14}
        value={draft.text}
        error={refusals.text}
        onChange={(event) => onChange({ text: event.currentTarget.value })}
      />
      <TagsInput
        label="Теги"
        description="метки выборки: цена, аудит, b2b — Enter после каждой"
        value={draft.tags}
        error={refusals.tags}
        onChange={(tags) => onChange({ tags })}
      />
      <Switch
        label="Агент видит запись"
        checked={draft.active}
        onChange={(event) => onChange({ active: event.currentTarget.checked })}
      />
    </>
  );
}

export function KbEntryModal({ entry, kinds, limits, onClose, onSaved }: Props) {
  const [draft, setDraft] = useState<KbDraft>(() => draftOf(entry, kinds[0] ?? 'brief'));
  // Тронутые поля: пустая новая форма не краснеет сразу — отказ «впишите»
  // встаёт под полем, которое правили и оставили пустым или слишком длинным.
  const [touched, setTouched] = useState<ReadonlySet<string>>(() => new Set());
  const save = useMutation({
    mutationFn: (body: KbDraft) =>
      entry === null ? addKbEntry(body) : changeKbEntry(entry.id, body),
    onSuccess: onSaved,
  });
  const refusals = refusalsOf(draft, limits);
  const ready = Object.keys(refusals).length === 0 && (entry === null || changed(draft, entry));
  const shown = Object.fromEntries(
    Object.entries(refusals).filter(([field]) => touched.has(field)),
  ) as typeof refusals;

  return (
    <Modal
      opened
      onClose={onClose}
      size="lg"
      title={entry === null ? 'Новая запись базы знаний' : `Запись №${entry.id}`}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (ready) save.mutate(draft);
        }}
      >
        <Stack gap="sm">
          {save.error !== null && (
            <Alert color="red" title="Не сохранили">
              {refusalOf(save.error)}
            </Alert>
          )}
          <KbFields
            draft={draft}
            kinds={kinds}
            limits={limits}
            refusals={shown}
            onChange={(patch) => {
              setDraft((current) => ({ ...current, ...patch }));
              setTouched((current) => new Set([...current, ...Object.keys(patch)]));
            }}
          />
          <Group justify="flex-end" mt="sm">
            <Button variant="subtle" className="press" onClick={onClose}>
              Отмена
            </Button>
            <Button type="submit" className="press" disabled={!ready} loading={save.isPending}>
              {entry === null ? 'Завести' : 'Сохранить'}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
