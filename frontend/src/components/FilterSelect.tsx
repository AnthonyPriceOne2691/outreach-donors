/**
 * Поле фильтра под заголовком колонки — списком «все» и значения колонки, у пункта — счётчик
 * сервера: «ушло · 12». В поле — только слово: число меняется с соседними фильтрами, а поле
 * говорит, что выбрано. Одно на таблицы с фильтрами в шапке — «Все рекламодатели», «Обход
 * доноров»: копии уже начали расходиться.
 */

import { Select } from '@mantine/core';

import { formatNumber } from '../format';

/** Стрелка списка в поле фильтра — уже умолчания, как у доноров. */
export const ARROW_WIDTH = 22;

/** «ушло · 12»: счётчик — в пункте списка, в поле — только слово. */
export function counted(title: string, count: number | undefined): string {
  return count === undefined ? title : `${title} · ${formatNumber(count)}`;
}

function options<T extends string>(keys: readonly T[], title: (key: T) => string) {
  return [
    { value: 'all', label: 'все' },
    ...keys.map((key) => ({ value: key, label: title(key) })),
  ];
}

export function FilterSelect<T extends string>({
  label,
  value,
  keys,
  title,
  count,
  onPick,
}: {
  label: string;
  value: T | null;
  keys: readonly T[];
  title: (key: T) => string;
  /** Счётчик пункта; `undefined` — без числа. */
  count: (key: T | 'all') => number | undefined;
  onPick: (value: T | null) => void;
}) {
  return (
    <Select
      size="xs"
      aria-label={label}
      rightSectionWidth={ARROW_WIDTH}
      allowDeselect={false}
      value={value ?? 'all'}
      onChange={(picked) => onPick(keys.find((key) => key === picked) ?? null)}
      data={options(keys, title)}
      renderOption={({ option }) => (
        <span>{counted(option.label, count(option.value as T | 'all'))}</span>
      )}
    />
  );
}
