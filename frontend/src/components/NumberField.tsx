/**
 * Поле числа: держит набранное строкой, а годится ли оно — решает правило поля
 * (`numberText.ts`) и говорит отказ словами.
 *
 * Обычное текстовое поле, а не `NumberInput`: тот молча выбрасывал из набора
 * всё, что не цифра, и склеивал оставшееся (проверка QA 10.10.2026). Стрелок
 * «больше / меньше» у поля поэтому нет — число в нём набирают.
 */

import { TextInput } from '@mantine/core';
import type { TextInputProps } from '@mantine/core';

import type { NumberRule } from './numberText';

interface Props extends Omit<TextInputProps, 'value' | 'onChange' | 'inputMode'> {
  /** Как набрано. */
  value: string;
  onChange: (text: string) => void;
  /** Целое или деньги: от этого — клавиатура телефона. */
  decimals?: NumberRule['decimals'];
  /**
   * Отказ — над полем, на месте пояснения. Для ряда, выровненного по низу
   * (поля рядом с кнопкой): строка отказа под полем поднимала бы его над
   * соседями, и ряд «плясал» бы — ровно то, против чего `fieldRow`.
   */
  refusalAbove?: boolean;
}

export function NumberField({
  value,
  onChange,
  decimals = 0,
  refusalAbove = false,
  error,
  description,
  ...field
}: Props) {
  const above = refusalAbove && Boolean(error);
  return (
    <TextInput
      {...field}
      // На телефоне — цифры: у целого поля без запятой, у денег — с ней.
      inputMode={decimals === 0 ? 'numeric' : 'decimal'}
      value={value}
      description={above ? error : description}
      // Цвет — как у отказа под полем: та же переменная, в каждой теме своя ступень (glass.css).
      descriptionProps={above ? { c: 'var(--mantine-color-error)' } : {}}
      error={above ? true : error}
      onChange={(event) => onChange(event.currentTarget.value)}
    />
  );
}
