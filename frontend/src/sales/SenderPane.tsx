/**
 * Отправитель продаж: от чьего имени письмо, чем подписано, куда звать лида.
 *
 * **Готовность — словами сервера.** Чего не хватает для отправки, называет сервер
 * тем же правилом, каким откажет сама отправка (Ф4): «не задан физический адрес»
 * и прочее. Экран не решает сам и поля правила не перечисляет — ни в «не готова»,
 * ни в «готова»: иначе правило сервера сменится, а экран скажет прежнее (так
 * «адрес и подпись заданы» пережило бы правило об имени отправителя).
 *
 * **Границы — сервера** (`limits`): длинное поле видно до нажатия, форму ссылок
 * и Telegram судит сервер, его отказ встаёт над формой целиком. Пустое поле
 * уходит `null` — «не задано», а не пустой строкой.
 */

import {
  Alert,
  Button,
  Group,
  Loader,
  SimpleGrid,
  Stack,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { SENDER_FIELDS } from '../api/salesLabels';
import type { SenderBody, SenderField, SenderView } from '../api/salesTypes';
import { formatDateTime, formatNumber } from '../format';
import { useSaveSender, useSender } from './kbData';
import { SaveRefusal } from './SaveRefusal';

const FIELD_KEYS = Object.keys(SENDER_FIELDS) as SenderField[];

type Draft = Record<SenderField, string>;

function draftOf(view: SenderBody): Draft {
  return Object.fromEntries(FIELD_KEYS.map((key) => [key, view[key] ?? ''])) as Draft;
}

function bodyOf(draft: Draft): SenderBody {
  const trimmed = (value: string) => (value.trim() === '' ? null : value);
  return Object.fromEntries(FIELD_KEYS.map((key) => [key, trimmed(draft[key])])) as SenderBody;
}

/** Длиннее предела сервера — словами под полем, до нажатия. */
function tooLong(draft: Draft, limits: SenderView['limits']): Partial<Record<SenderField, string>> {
  const found = FIELD_KEYS.filter((key) => draft[key].trim().length > limits[key]).map((key) => [
    key,
    `Длиннее ${formatNumber(limits[key])} знаков: сейчас ${formatNumber(draft[key].trim().length)}`,
  ]);
  return Object.fromEntries(found) as Partial<Record<SenderField, string>>;
}

function Readiness({ missing }: { missing: string[] }) {
  if (missing.length === 0) {
    return (
      <Alert color="green" title="Отправка продаж готова">
        Всё, без чего письмо продаж не уходит, задано.
      </Alert>
    );
  }
  return (
    <Alert color="yellow" title="Отправка продаж не готова">
      {missing.join('; ')} — без этого письмо продаж не уходит.
    </Alert>
  );
}

function SenderInput({
  field,
  value,
  error,
  onChange,
}: {
  field: SenderField;
  value: string;
  error: string | undefined;
  onChange: (value: string) => void;
}) {
  const { label, hint, multiline } = SENDER_FIELDS[field];
  const props = { label, description: hint, value, error };
  return multiline ? (
    <Textarea
      {...props}
      autosize
      minRows={3}
      maxRows={8}
      onChange={(event) => onChange(event.currentTarget.value)}
    />
  ) : (
    <TextInput {...props} onChange={(event) => onChange(event.currentTarget.value)} />
  );
}

function SenderForm({ view }: { view: SenderView }) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(view));
  const save = useSaveSender();
  // Граница `md` у Mantine — 62em: шире поля стоят парой.
  const paired = useMediaQuery('(min-width: 62em)');
  const refusals = tooLong(draft, view.limits);
  const dirty = JSON.stringify(bodyOf(draft)) !== JSON.stringify(bodyOf(draftOf(view)));
  const ready = dirty && Object.keys(refusals).length === 0;

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) save.mutate(bodyOf(draft));
      }}
    >
      <Stack gap="md">
        <Readiness missing={view.missing} />
        <SaveRefusal error={save.error} />
        {/* Две строки под пояснение (`fieldRow`) — только когда поля стоят парой:
            в один столбец ряда нет, и пустая строка под каждым пояснением
            растягивала форму на телефоне (снимок 390 px). */}
        <SimpleGrid
          cols={{ base: 1, md: 2 }}
          spacing="md"
          px="md"
          className={paired ? 'fieldRow' : ''}
        >
          {FIELD_KEYS.map((field) => (
            <SenderInput
              key={field}
              field={field}
              value={draft[field]}
              error={refusals[field]}
              onChange={(value) => setDraft((current) => ({ ...current, [field]: value }))}
            />
          ))}
        </SimpleGrid>
        <Group justify="space-between" align="center" wrap="wrap" gap="sm" px="md">
          <Text size="xs" c="dimmed">
            {view.updated_by === null || view.updated_at === null
              ? 'Ещё не заполнялся.'
              : `Правил ${view.updated_by} · ${formatDateTime(view.updated_at)}`}
          </Text>
          <Button type="submit" className="press" disabled={!ready} loading={save.isPending}>
            Сохранить
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

export function SenderPane() {
  const sender = useSender();
  if (sender.data !== undefined) {
    // Ключ — время правки: сохранённое приходит новым ответом, и форма встаёт
    // от него, а не держит набранное поверх прочитанного.
    return <SenderForm key={sender.data.updated_at ?? 'new'} view={sender.data} />;
  }
  if (sender.error) {
    return (
      <Alert color="red" title="Отправитель не загрузился">
        {refusalOf(sender.error)}
      </Alert>
    );
  }
  return <Loader aria-label="Загружаем отправителя" m="md" />;
}
