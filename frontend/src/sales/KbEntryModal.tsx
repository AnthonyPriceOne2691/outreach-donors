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
 *
 * **Без права отправки писем окно — смотреть** (`WriteRight`): поля и «Сохранить» закрыты.
 */

import {
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

import { addKbEntry, changeKbEntry } from '../api/sales';
import { KB_KINDS, kbKindTitle } from '../api/salesLabels';
import type { KbEntryCard, KbKind, KbLimits } from '../api/salesTypes';
import { formatNumber } from '../format';
import { changed, draftOf, refusalsOf } from './kbDraft';
import type { KbDraft } from './kbDraft';
import { SaveRefusal } from './SaveRefusal';
import { WriteRight } from './WriteRight';

interface Props {
  /** Правка — запись; новая — `null`. */
  entry: KbEntryCard | null;
  kinds: KbKind[];
  limits: KbLimits;
  /** Право записи (`WriteRight`); без него окно — смотреть. */
  mayWrite: boolean;
  onClose: () => void;
  onSaved: (saved: KbEntryCard) => void;
}

/** Вид — коротким словом в поле: на телефоне длинная подпись резалась краем поля
 *  («о компании — кто мы и что делаем — фо…»). Что в такую запись пишут — под
 *  полем, для выбранного вида. */
function kindOptions(kinds: KbKind[]) {
  return kinds.map((kind) => ({ value: kind, label: kbKindTitle(kind) }));
}

/** Поля записи. Ошибки — уже посчитанные: форма их только показывает. */
function KbFields({
  draft,
  kinds,
  limits,
  refusals,
  disabled,
  onChange,
}: {
  draft: KbDraft;
  kinds: KbKind[];
  limits: KbLimits;
  refusals: ReturnType<typeof refusalsOf>;
  disabled: boolean;
  onChange: (patch: Partial<KbDraft>) => void;
}) {
  return (
    <>
      <Select
        label="Вид"
        description={KB_KINDS[draft.kind].hint}
        allowDeselect={false}
        data={kindOptions(kinds)}
        value={draft.kind}
        disabled={disabled}
        onChange={(value) => value !== null && onChange({ kind: value as KbKind })}
      />
      <TextInput
        label="Язык"
        description="код языка письма: ru, en, pt-br"
        value={draft.language}
        error={refusals.language}
        disabled={disabled}
        onChange={(event) => onChange({ language: event.currentTarget.value })}
      />
      <TextInput
        label="Заголовок"
        description="по виду, языку и заголовку запись узнаёт и повторная загрузка файла"
        value={draft.title}
        error={refusals.title}
        disabled={disabled}
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
        disabled={disabled}
        onChange={(event) => onChange({ text: event.currentTarget.value })}
      />
      <TagsInput
        label="Теги"
        description="метки выборки: цена, аудит, b2b — Enter после каждой"
        value={draft.tags}
        error={refusals.tags}
        disabled={disabled}
        onChange={(tags) => onChange({ tags })}
      />
      <Switch
        label="Агент видит запись"
        checked={draft.active}
        disabled={disabled}
        onChange={(event) => onChange({ active: event.currentTarget.checked })}
      />
    </>
  );
}

export function KbEntryModal({ entry, kinds, limits, mayWrite, onClose, onSaved }: Props) {
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
  const ready =
    mayWrite && Object.keys(refusals).length === 0 && (entry === null || changed(draft, entry));
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
          <SaveRefusal error={save.error} />
          <KbFields
            draft={draft}
            kinds={kinds}
            limits={limits}
            refusals={shown}
            disabled={!mayWrite}
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
          {!mayWrite && <WriteRight what="Правит базу знаний" />}
        </Stack>
      </form>
    </Modal>
  );
}
