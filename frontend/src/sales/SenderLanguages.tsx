/**
 * Наборы «Отправителя» по языку письма — под общими полями, блоком на каждый язык писем.
 *
 * **Языки — данными сервера** (`languages` ответа, в порядке языков цепочек), и заголовок блока
 * («Письма на английском») — словами сервера: новый язык цепочек встанет здесь без правки экрана.
 * **Все поля необязательны**: пустое — в письмах на этом языке общее поле, так и сказано под
 * заголовком. Пределы и многострочность — общих полей (`limits` сервера, `SENDER_FIELDS`);
 * пояснений общих полей у полей набора нет: «обязателен» и «без него не уходит» — про общий набор.
 * Отказ сервера встаёт над формой целиком, как у общих полей.
 *
 * **Поле набора называется с языком** для программы чтения с экрана («Подпись — письма на
 * английском»): подписи полей в блоках те же, что у общих, а блок — группа с заголовком.
 *
 * **Пустой блок свёрнут** (решение 21.10.2026: вкладка на телефоне выросла почти вдвое):
 * заголовок — кнопка «Письма на английском», рядом пометка «не заполнено — общее». Блок, где
 * задано хоть одно поле, открыт; блок, где что-то не так с полем, открывается сам — иначе
 * «Сохранить» закрыта без видимой причины. Закрытое не отрисовывается (`Unfold`).
 */

import { Button, Group, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { IconChevronDown } from '@tabler/icons-react';
import { useId, useState } from 'react';

import { SENDER_FIELDS } from '../api/salesLabels';
import type {
  SenderLanguageBody,
  SenderLanguageCard,
  SenderLanguageField,
} from '../api/salesSenderTypes';
import type { SenderView } from '../api/salesTypes';
import { Unfold } from '../runs/Unfold';
import { refusalsOf, SenderInput } from './SenderInput';

/** Поля набора — в порядке формы (`sender.LANGUAGE_FIELDS` сервера). */
export const LANGUAGE_FIELDS: readonly SenderLanguageField[] = [
  'sender_name',
  'sender_position',
  'signature',
  'physical_address',
];

export const LANGUAGE_HINT = 'Пусто — в письмах на этом языке будет общее поле';
/** Пометка у свёрнутого пустого блока: письма на этом языке подписаны общим набором. */
export const EMPTY_MARK = 'не заполнено — общее';

type SetDraft = Record<SenderLanguageField, string>;
/** Набранное в блоках: код языка → поле → текст. */
export type SetsDraft = Record<string, SetDraft>;
type SetRefusals = Partial<Record<SenderLanguageField, string>>;

const NO_SET = Object.fromEntries(LANGUAGE_FIELDS.map((key) => [key, ''])) as SetDraft;

/** Набранное в блоке языка; блока ещё нет — пустые поля. */
export function setOf(sets: SetsDraft, language: string): SetDraft {
  return sets[language] ?? NO_SET;
}

export function setsOf(cards: SenderLanguageCard[]): SetsDraft {
  return Object.fromEntries(
    cards.map((card) => [
      card.language,
      Object.fromEntries(LANGUAGE_FIELDS.map((key) => [key, card[key] ?? ''])) as SetDraft,
    ]),
  );
}

/** Наборы в тело записи: пустое поле уходит `null` — «общее поле». */
export function setsBody(sets: SetsDraft): Record<string, SenderLanguageBody> {
  const trimmed = (value: string) => (value.trim() === '' ? null : value);
  return Object.fromEntries(
    Object.entries(sets).map(([code, set]) => [
      code,
      Object.fromEntries(
        LANGUAGE_FIELDS.map((key) => [key, trimmed(set[key])]),
      ) as SenderLanguageBody,
    ]),
  );
}

/** Что не так с полями наборов до нажатия — по языкам (`refusalsOf`); пусто — всё в порядке. */
export function setsRefusals(
  sets: SetsDraft,
  view: Pick<SenderView, 'limits' | 'unfilled'>,
): Record<string, SetRefusals> {
  return Object.fromEntries(
    Object.entries(sets).map(([code, set]) => [code, refusalsOf(set, LANGUAGE_FIELDS, view)]),
  );
}

/** Задано ли в наборе хоть одно поле. */
function filled(set: Record<SenderLanguageField, string | null>): boolean {
  return LANGUAGE_FIELDS.some((key) => (set[key] ?? '').trim() !== '');
}

function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}

function Toggle({
  title,
  shown,
  bodyId,
  onToggle,
}: {
  title: string;
  shown: boolean;
  bodyId: string;
  onToggle: () => void;
}) {
  return (
    <Button
      variant="subtle"
      size="compact-sm"
      px={6}
      className="press senderLanguageToggle"
      leftSection={
        <IconChevronDown
          size={16}
          style={{
            transform: shown ? 'rotate(180deg)' : 'none',
            transition: 'transform 200ms cubic-bezier(0.32, 0.72, 0, 1)',
          }}
        />
      }
      aria-expanded={shown}
      aria-controls={bodyId}
      onClick={onToggle}
    >
      {title}
    </Button>
  );
}

function LanguageBlock({
  card,
  set,
  refusals,
  disabled,
  paired,
  onChange,
}: {
  card: SenderLanguageCard;
  set: SetDraft;
  refusals: SetRefusals;
  disabled: boolean;
  paired: boolean;
  onChange: (field: SenderLanguageField, value: string) => void;
}) {
  const id = useId();
  const bodyId = `${id}-body`;
  // Открыт — набор задан в сохранённом; пустой свёрнут. Отказ поля открывает блок сам.
  const [open, setOpen] = useState(() => filled(card));
  const shown = open || Object.keys(refusals).length > 0;
  return (
    <Stack gap="xs" px="md" role="group" aria-labelledby={id} className="senderLanguage">
      <Group gap="xs" align="center" wrap="wrap">
        <Title order={5} id={id}>
          <Toggle
            title={card.title}
            shown={shown}
            bodyId={bodyId}
            onToggle={() => setOpen(!shown)}
          />
        </Title>
        {!filled(set) && (
          <Text size="xs" c="dimmed" className="senderLanguageMark">
            {EMPTY_MARK}
          </Text>
        )}
      </Group>
      <Unfold open={shown}>
        <Stack gap="xs" id={bodyId}>
          <Text size="xs" c="dimmed" className="senderLanguageHint">
            {LANGUAGE_HINT}
          </Text>
          <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md" className={paired ? 'fieldRow' : ''}>
            {LANGUAGE_FIELDS.map((field) => (
              <SenderInput
                key={field}
                field={field}
                value={set[field]}
                error={refusals[field]}
                disabled={disabled}
                description={null}
                name={`${SENDER_FIELDS[field].label} — ${lowerFirst(card.title)}`}
                onChange={(value) => onChange(field, value)}
              />
            ))}
          </SimpleGrid>
        </Stack>
      </Unfold>
    </Stack>
  );
}

export function SenderLanguages({
  cards,
  sets,
  refusals,
  disabled,
  paired,
  onChange,
}: {
  cards: SenderLanguageCard[];
  sets: SetsDraft;
  refusals: Record<string, SetRefusals>;
  disabled: boolean;
  paired: boolean;
  onChange: (language: string, field: SenderLanguageField, value: string) => void;
}) {
  return cards.map((card) => (
    <LanguageBlock
      key={card.language}
      card={card}
      set={setOf(sets, card.language)}
      refusals={refusals[card.language] ?? {}}
      disabled={disabled}
      paired={paired}
      onChange={(field, value) => onChange(card.language, field, value)}
    />
  ));
}
