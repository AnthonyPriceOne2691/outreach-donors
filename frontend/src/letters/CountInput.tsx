/**
 * Число формы сборки, которое показывает то, что уйдёт на сервер (проверка QA 10.10.2026).
 *
 * Поле Mantine отдаёт строку, пока число не набрано: пусто — или то, что ещё не число.
 * Форма подменяла её умолчанием прямо в `onChange`, и поле расходилось с формой:
 * очищенная «Добивка 1» стояла пустой, а уходили 7 дней; «За раз» после букв — пустым,
 * а уходили 50. А когда умолчание отличалось от прежнего числа, оно вставало в поле
 * посреди набора, и цифры дописывались к нему.
 *
 * Здесь набранное живёт в поле как есть, а форма получает только целое в границах.
 * Ушли из поля — пустое становится умолчанием, число — числом в границах (его Mantine
 * прижимает сам): что видно, то и уйдёт. Числа целые — точку и минус поле не берёт.
 */

import { NumberInput } from '@mantine/core';
import type { NumberInputProps } from '@mantine/core';
import { clamp } from '@mantine/hooks';
import { useRef, useState } from 'react';

type FieldProps = Omit<
  NumberInputProps,
  'value' | 'defaultValue' | 'onChange' | 'onBlur' | 'min' | 'max'
>;

interface Props extends FieldProps {
  /** Число, которое уйдёт сейчас. */
  value: number;
  /** Что уйдёт, если поле оставить пустым. */
  fallback: number;
  min: number;
  max: number;
  onValue: (value: number) => void;
}

export function CountInput({ value, fallback, min, max, onValue, ...field }: Props) {
  const [text, setText] = useState<number | string>(value);
  // Последнее, что отдало поле. Уходя из поля, Mantine прижимает число к границам раньше,
  // чем зовёт `onBlur`, и значение из замыкания там уже устарело бы.
  const latest = useRef<number | string>(value);
  const take = (next: number | string) => {
    latest.current = next;
    setText(next);
    if (typeof next === 'number') onValue(clamp(next, min, max));
  };
  return (
    <NumberInput
      {...field}
      value={text}
      min={min}
      max={max}
      allowDecimal={false}
      allowNegative={false}
      onChange={take}
      onBlur={() => {
        if (typeof latest.current !== 'number') take(fallback);
      }}
    />
  );
}
