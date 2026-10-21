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
 * и Telegram судит сервер, его отказ встаёт над формой целиком. Подстановка в поле
 * (`{{name}}`, «… НЕ ЗАДАНО») тоже видна под полем до нажатия — по образцу сервера
 * (`unfilled`): поле уходит в письмо как есть. Пустое поле уходит `null` — «не задано»,
 * а не пустой строкой.
 *
 * **Без права отправки писем форма — смотреть** (`WriteRight`): поля и «Сохранить» закрыты.
 *
 * **Под общими полями — набор на каждый язык писем** (`SenderLanguages`, находка стенда
 * 21.10.2026: английское письмо уходило с русскими подписью, адресом и именем в From). Одна
 * форма и одно «Сохранить»: общие поля и наборы уходят телом целиком, как их пишет сервер.
 */

import { Alert, Button, Group, Loader, SimpleGrid, Stack, Text } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useState } from 'react';

import { refusalOf } from '../api/client';
import { SENDER_FIELDS } from '../api/salesLabels';
import type { SenderBody, SenderField, SenderView } from '../api/salesTypes';
import { formatDateTime } from '../format';
import { useSaveSender, useSender } from './kbData';
import { SaveRefusal } from './SaveRefusal';
import { refusalsOf, SenderInput } from './SenderInput';
import {
  SenderLanguages,
  setOf,
  setsBody,
  setsOf,
  setsRefusals,
  type SetsDraft,
} from './SenderLanguages';
import { useMayWrite, WriteRight } from './WriteRight';

const FIELD_KEYS = Object.keys(SENDER_FIELDS) as SenderField[];

type Draft = Record<SenderField, string>;

function draftOf(view: SenderView): Draft {
  return Object.fromEntries(FIELD_KEYS.map((key) => [key, view[key] ?? ''])) as Draft;
}

/** Тело записи целиком: общие поля и наборы языков; пустое уходит `null` — «не задано». */
function bodyOf(draft: Draft, sets: SetsDraft): SenderBody {
  const trimmed = (value: string) => (value.trim() === '' ? null : value);
  const fields = Object.fromEntries(FIELD_KEYS.map((key) => [key, trimmed(draft[key])]));
  return { ...(fields as Record<SenderField, string | null>), languages: setsBody(sets) };
}

/** Есть ли хоть один отказ «длиннее» — в общих полях или в наборах языков. */
function refused(...found: object[]): boolean {
  return found.some((refusals) => Object.keys(refusals).length > 0);
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

function SenderForm({ view }: { view: SenderView }) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(view));
  const [sets, setSets] = useState<SetsDraft>(() => setsOf(view.languages));
  const save = useSaveSender();
  const mayWrite = useMayWrite();
  // Граница `md` у Mantine — 62em: шире поля стоят парой.
  const paired = useMediaQuery('(min-width: 62em)') ?? false;
  const refusals = refusalsOf(draft, FIELD_KEYS, view);
  const setRefusals = setsRefusals(sets, view);
  const body = bodyOf(draft, sets);
  const dirty =
    JSON.stringify(body) !== JSON.stringify(bodyOf(draftOf(view), setsOf(view.languages)));
  const ready = mayWrite && dirty && !refused(refusals, ...Object.values(setRefusals));

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) save.mutate(body);
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
              disabled={!mayWrite}
              onChange={(value) => setDraft((current) => ({ ...current, [field]: value }))}
            />
          ))}
        </SimpleGrid>
        <SenderLanguages
          cards={view.languages}
          sets={sets}
          refusals={setRefusals}
          disabled={!mayWrite}
          paired={paired}
          onChange={(language, field, value) =>
            setSets((current) => ({
              ...current,
              [language]: { ...setOf(current, language), [field]: value },
            }))
          }
        />
        {/* «Сохранить» — слева, как у порогов и настроек агента: одно действие стоит
            в одном месте на всех формах (аудит экранов 09.10.2026). */}
        <Group align="center" wrap="wrap" gap="sm" px="md">
          <Button type="submit" className="press" disabled={!ready} loading={save.isPending}>
            Сохранить
          </Button>
          <Text size="xs" c="dimmed">
            {view.updated_by === null || view.updated_at === null
              ? 'Ещё не заполнялся.'
              : `Правил ${view.updated_by} · ${formatDateTime(view.updated_at)}`}
          </Text>
        </Group>
        {!mayWrite && <WriteRight what="Правит отправителя" px="md" />}
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
