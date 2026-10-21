/**
 * Поле «Отправителя» — общее и набора языка письма одним видом: подпись поля и многострочность —
 * `SENDER_FIELDS`. Что не так с полем, видно под ним до нажатия: длиннее предела сервера (`limits`)
 * или в поле подстановка (образец — сервера, `unfilled`): поле уходит в письмо как есть, и
 * подстановку в нём никто не заменит. Сервер отказывает тем же правилом при записи.
 */

import { Textarea, TextInput } from '@mantine/core';

import { SENDER_FIELDS } from '../api/salesLabels';
import type { SenderField, SenderView } from '../api/salesTypes';
import { formatNumber } from '../format';

/** Почему подстановка — отказ: те же слова, что у сервера (`sender.UNFILLED_WORDS`). */
export const UNFILLED_WORDS =
  'подстановки здесь не работают: поле уходит в письмо как есть, напишите текст целиком';

function quoted(found: string): string {
  return found.startsWith('«') ? found : `«${found}»`;
}

/** Что не так с полем — словами под ним, до нажатия; пусто — всё в порядке. */
function refusalOf(value: string, limit: number, unfilled: RegExp): string | null {
  const text = value.trim();
  if (text.length > limit) {
    return `Длиннее ${formatNumber(limit)} знаков: сейчас ${formatNumber(text.length)}`;
  }
  const left = unfilled.exec(text);
  return left === null ? null : `${quoted(left[0])} — ${UNFILLED_WORDS}`;
}

/** Отказы полей до нажатия: поле → слова. */
export function refusalsOf<K extends SenderField>(
  values: Record<K, string>,
  keys: readonly K[],
  view: Pick<SenderView, 'limits' | 'unfilled'>,
): Partial<Record<K, string>> {
  const unfilled = new RegExp(view.unfilled);
  const found = keys
    .map((key) => [key, refusalOf(values[key], view.limits[key], unfilled)] as const)
    .filter(([, words]) => words !== null);
  return Object.fromEntries(found) as Partial<Record<K, string>>;
}

export function SenderInput({
  field,
  value,
  error,
  disabled,
  onChange,
  description,
  name,
}: {
  field: SenderField;
  value: string;
  error: string | undefined;
  disabled: boolean;
  onChange: (value: string) => void;
  /** Пояснение под подписью; `null` — без пояснения, не задано — пояснение общего поля. */
  description?: string | null;
  /** Имя поля для программы чтения с экрана, если подписи мало (поле набора языка). */
  name?: string;
}) {
  const { label, hint, multiline } = SENDER_FIELDS[field];
  const said = description === undefined ? hint : (description ?? undefined);
  const props = { label, description: said, value, error, disabled, 'aria-label': name };
  return multiline ? (
    <Textarea
      {...props}
      autosize
      minRows={3}
      maxRows={8}
      onChange={(event) => onChange(event.currentTarget.value)}
    />
  ) : (
    // Одна строка — имя, должность, ссылка: поле по значению, а не на половину панели
    // (аудит экранов 09.10.2026). Подписи и адресу в несколько строк ширина нужна.
    <TextInput {...props} maw="22rem" onChange={(event) => onChange(event.currentTarget.value)} />
  );
}
