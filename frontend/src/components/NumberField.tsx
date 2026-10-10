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
import { useLayoutEffect, useRef } from 'react';
import type { ChangeEvent } from 'react';

import { groupDigits } from './numberText';
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
  /** Разряды пробелом по ходу набора, как у чисел на экране: «1 000 000»
   *  читается, «1000000» — пересчитывают по нулям. Только у набранного из
   *  одних цифр: с буквой или точкой поле стоит как набрано. */
  grouped?: boolean;
}

/** Где встать курсору после перестановки пробелов: за той же по счёту цифрой. */
function caretAfter(text: string, digits: number): number {
  if (digits === 0) return 0;
  let seen = 0;
  for (let at = 0; at < text.length; at += 1) {
    if (text[at] !== ' ') seen += 1;
    if (seen === digits) return at + 1;
  }
  return text.length;
}

/** Набор в поле с разрядами: пробелы встают на место, курсор — за той же
 *  цифрой. React, сменив значение поля, ставит курсор в конец — без этого
 *  правка середины «1 000 000» уводила бы его в хвост на каждой цифре. */
function useGrouped(grouped: boolean, onChange: (text: string) => void) {
  const input = useRef<HTMLInputElement>(null);
  const caret = useRef<{ text: string; at: number } | null>(null);
  useLayoutEffect(() => {
    const want = caret.current;
    if (want === null || input.current?.value !== want.text) return;
    input.current.setSelectionRange(want.at, want.at);
    caret.current = null;
  });
  const change = (event: ChangeEvent<HTMLInputElement>) => {
    const typed = event.currentTarget.value;
    const regrouped = grouped ? groupDigits(typed) : null;
    if (regrouped !== null && regrouped !== typed) {
      const end = event.currentTarget.selectionStart ?? typed.length;
      const digits = typed.slice(0, end).replace(/\s/g, '').length;
      caret.current = { text: regrouped, at: caretAfter(regrouped, digits) };
    }
    onChange(regrouped ?? typed);
  };
  return { input, change };
}

export function NumberField({
  value,
  onChange,
  decimals = 0,
  refusalAbove = false,
  grouped = false,
  error,
  description,
  ...field
}: Props) {
  const { input, change } = useGrouped(grouped, onChange);
  const above = refusalAbove && Boolean(error);
  return (
    <TextInput
      {...field}
      ref={input}
      // На телефоне — цифры: у целого поля без запятой, у денег — с ней.
      inputMode={decimals === 0 ? 'numeric' : 'decimal'}
      value={value}
      description={above ? error : description}
      // Цвет — как у отказа под полем: та же переменная, в каждой теме своя ступень (glass.css).
      descriptionProps={above ? { c: 'var(--mantine-color-error)' } : {}}
      error={above ? true : error}
      onChange={change}
    />
  );
}
