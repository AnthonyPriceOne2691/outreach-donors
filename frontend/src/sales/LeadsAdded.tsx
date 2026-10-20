/**
 * Колонка «Дата добавления» на вкладке «emails» — когда лид появился в сервисе, — и её фильтр:
 * период «с … по …» днями, оба дня включительно (`leadPeriod.ts`).
 *
 * **Фильтр — под своей колонкой**, как все фильтры таблицы: два поля даты одно под другим, «с»
 * и «по» — словом перед полем. День набирают по частям, поэтому в адрес он уходит после паузы в
 * наборе (`useTyped`), как поиск: иначе каждая цифра года — запрос и мигание таблицы, а
 * недонабранный день адрес не трогает.
 *
 * **«С» позже «по» — словами под полями** и рамкой обоих полей; запроса нет (`useLeads`), вместо
 * строк — что поправить (`backwardsOf`).
 *
 * Дата в строке — форматом остальных таблиц продаж (`formatDate`, «03.10.2026») и по часам
 * браузера: теми же сутками, что у фильтра.
 */

import { Group, Input, Stack, Table, Text, TextInput } from '@mantine/core';
import { useId } from 'react';

import type { LeadCard } from '../api/salesTypes';
import { useTyped } from '../donors/useTyped';
import { formatDate } from '../format';
import type { LeadFilters } from './leadFilters';
import { addedDayOf, addedProblem, sameAddedDay } from './leadPeriod';

/** Поле даты — по значению «28.10.2026» со значком календаря браузера: в 6,5rem значок
 *  срезал последнюю цифру, в 6,75rem вставал к ней вплотную (замер на стенде, 20.10.2026). */
export const ADDED_FIELD = '7rem';
/** Слово перед полем — по «по»: 14 px шрифтом поля. */
const WORD = '1rem';

interface DayProps {
  word: string;
  /** Имя поля для программы чтения с экрана: «Добавлены с». */
  name: string;
  value: string;
  /** Период задан наоборот: рамка отказа и слова под полями. */
  problemId: string | null;
  onChange: (value: string) => void;
}

function DayField({ word, name, value, problemId, onChange }: DayProps) {
  const id = useId();
  return (
    <Group gap={4} wrap="nowrap" justify="center">
      <Text component="label" htmlFor={id} size="xs" w={WORD} ta="right">
        {word}
      </Text>
      <TextInput
        id={id}
        type="date"
        size="xs"
        w={ADDED_FIELD}
        aria-label={name}
        aria-describedby={problemId ?? undefined}
        value={value}
        error={problemId !== null}
        onChange={(event) => onChange(event.currentTarget.value)}
      />
    </Group>
  );
}

interface FilterProps {
  filters: LeadFilters;
  onFilter: (patch: Partial<LeadFilters>) => void;
}

/** «С» и «по» под заголовком колонки; набранное — в адрес после паузы. */
export function AddedFilter({ filters, onFilter }: FilterProps) {
  const problemId = useId();
  const [from, setFrom] = useTyped(
    filters.addedFrom,
    (value) => onFilter({ addedFrom: addedDayOf(value) }),
    sameAddedDay,
  );
  const [to, setTo] = useTyped(
    filters.addedTo,
    (value) => onFilter({ addedTo: addedDayOf(value) }),
    sameAddedDay,
  );
  const problem = addedProblem(filters.addedFrom, filters.addedTo);
  const said = problem === null ? null : problemId;
  return (
    <Stack gap={4} align="center">
      <DayField word="с" name="Добавлены с" value={from} problemId={said} onChange={setFrom} />
      <DayField word="по" name="Добавлены по" value={to} problemId={said} onChange={setTo} />
      {problem !== null && (
        // Ячейки шапки не переносят строку — слова под полями переносятся в ширину колонки.
        // Кегль — как у текста поля, 12 px: умолчание поля xs (10 px) читалось с трудом
        // (снимок стенда 20.10.2026).
        <Input.Error
          id={problemId}
          size="sm"
          ta="center"
          className="addedProblem"
          style={{ whiteSpace: 'normal' }}
        >
          {problem}
        </Input.Error>
      )}
    </Stack>
  );
}

/** Когда лид появился в сервисе — днём по часам браузера. */
export function AddedCell({ row }: { row: LeadCard }) {
  return (
    <Table.Td>
      <Text size="sm" className="leadAdded">
        {formatDate(row.created_at)}
      </Text>
    </Table.Td>
  );
}
