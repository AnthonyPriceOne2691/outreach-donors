/**
 * Поле фильтра под заголовком колонки — списком «все» и значения колонки, у пункта — счётчик
 * сервера: «ушло · 12». В поле — только слово: число меняется с соседними фильтрами, а поле
 * говорит, что выбрано. Одно на таблицы с фильтрами в шапке — «Все рекламодатели», «Обход
 * доноров», очередь проверки: копии уже начали расходиться.
 */

import { CheckIcon, Combobox, Select } from '@mantine/core';

import { formatNumber } from '../format';
import { dropdownBelow } from '../theme';

/** Стрелка списка в поле фильтра — уже умолчания, как у доноров. */
export const ARROW_WIDTH = 22;

/** Список по своему содержимому, левым краем по полю: в поле уже пункта со счётчиком тот рвался
 *  бы на две строки. */
const CONTENT_LIST = { ...dropdownBelow, width: 'max-content' } as const;

/** Галочка выбранного пункта — тем же классом, что в списках Mantine: у пункта со своей разметкой
 *  её нет сама собой, а место под неё резервирует только невыбранный, и пункты вставали лесенкой. */
const CHECK_ICON = (Combobox.classes as Record<string, string | undefined>)
  .optionsDropdownCheckIcon;

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
  width,
  contentList = false,
}: {
  label: string;
  value: T | null;
  keys: readonly T[];
  title: (key: T) => string;
  /** Счётчик пункта; `undefined` — без числа. */
  count: (key: T | 'all') => number | undefined;
  onPick: (value: T | null) => void;
  /** Ширина поля под широкой колонкой; без неё — вся колонка. */
  width?: string;
  /** Список по содержимому, а не по ширине поля, с галочкой у выбранного пункта. */
  contentList?: boolean;
}) {
  return (
    <Select
      size="xs"
      aria-label={label}
      {...(width !== undefined ? { w: width } : {})}
      {...(contentList ? { comboboxProps: CONTENT_LIST } : {})}
      rightSectionWidth={ARROW_WIDTH}
      allowDeselect={false}
      value={value ?? 'all'}
      onChange={(picked) => onPick(keys.find((key) => key === picked) ?? null)}
      data={options(keys, title)}
      renderOption={({ option, checked }) => (
        <>
          {contentList && checked === true ? <CheckIcon className={CHECK_ICON} /> : null}
          <span>{counted(option.label, count(option.value as T | 'all'))}</span>
        </>
      )}
    />
  );
}
