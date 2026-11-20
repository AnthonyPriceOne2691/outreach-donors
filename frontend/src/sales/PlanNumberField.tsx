/**
 * Числовое поле окон плана — окна загрузки (общая цель, потолки) и правки потолков после сохранения
 * («Качество сайтов», «Потолок плана»): в поле — как набрано, наверх — число или `null`.
 *
 * Набранное хранит само поле, а не окно: поле, перерисованное из числа, стирало точку на ходу — «0.5»
 * по знаку становилось «5», а «1.0» — пустым полем и отказом «потолок плана обязателен» (проверка
 * плана на проде 19.11.2026). То же правило у целых полей сборки писем — `letters/CountInput.tsx`;
 * здесь числа дробные, а пустое поле — «не задано», без умолчания.
 *
 * **Ошибку поля окно даёт словами** (`error`, для потолков — `capError`), а поле показывает её после
 * первого ухода из поля: на ходу «0» перед «0,5» мигал бы отказом. Без слов «Сохранить» у пустого
 * потолка закрывалась молча (стенд 20.11).
 */

import { NumberInput } from '@mantine/core';
import type { NumberInputProps } from '@mantine/core';
import { useState } from 'react';

/** Слова ошибки потолка — те же, что у сервера (`plan_actions.CAP_ABOVE_ZERO`). */
export const CAP_NEEDED = 'Потолок — в долларах, больше нуля';

/** Ошибка поля потолка: пусто или не больше нуля — слова, иначе нет. */
export function capError(cap: number | null): string | undefined {
  return cap === null || cap <= 0 ? CAP_NEEDED : undefined;
}

/** Число поля; пусто или не число — `null`. Mantine отдаёт строкой значение с точкой или нулём в
 * конце («0.», «1.0», «0.50») — это тоже число. */
export function numberOf(value: string | number): number | null {
  if (typeof value === 'number') return value;
  const parsed = value.trim() === '' ? Number.NaN : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

type Props = Omit<NumberInputProps, 'value' | 'onChange'> & {
  value: number | null;
  onValue: (value: number | null) => void;
};

export function PlanNumberField({ value, onValue, error, onBlur, ...props }: Props) {
  const [typed, setTyped] = useState<string | number>(value ?? '');
  const [left, setLeft] = useState(false);
  return (
    <NumberInput
      {...props}
      value={typed}
      error={left ? error : undefined}
      onBlur={(event) => {
        setLeft(true);
        onBlur?.(event);
      }}
      onChange={(next) => {
        setTyped(next);
        onValue(numberOf(next));
      }}
    />
  );
}
