/**
 * Числовое поле окон плана — окна загрузки (общая цель, потолки) и правки потолков после сохранения
 * («Качество сайтов», «Потолок плана»): в поле — как набрано, наверх — число или `null`.
 *
 * Набранное хранит само поле, а не окно: поле, перерисованное из числа, стирало точку на ходу — «0.5»
 * по знаку становилось «5», а «1.0» — пустым полем и отказом «потолок плана обязателен» (проверка
 * плана на проде 19.11.2026). То же правило у целых полей сборки писем — `letters/CountInput.tsx`;
 * здесь числа дробные, а пустое поле — «не задано», без умолчания.
 */

import { NumberInput } from '@mantine/core';
import type { NumberInputProps } from '@mantine/core';
import { useState } from 'react';

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

export function PlanNumberField({ value, onValue, ...props }: Props) {
  const [typed, setTyped] = useState<string | number>(value ?? '');
  return (
    <NumberInput
      {...props}
      value={typed}
      onChange={(next) => {
        setTyped(next);
        onValue(numberOf(next));
      }}
    />
  );
}
