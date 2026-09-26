/**
 * Таблица отбора с фильтрами под заголовками — каждый под своей колонкой.
 *
 * **Фильтр живёт у колонки, которую сужает** (замечание 26.09.2026: «для
 * таблицы добавь фильтры вместо тумблеров выше»). Поиск — под доменом;
 * пороги, судья, ответ донора и человек — под своими колонками, тем же
 * словом, что стоит в их ячейках. Четыре переключателя над таблицей стали
 * тремя фильтрами: «Только расхождения» и «Человек не смотрел» друг друга
 * исключали и слились в «Человека», «Судья не смотрел» и прежний выбор
 * «Кто решил у судьи» — в «Судью».
 *
 * **Поле не шире своих значений.** Замечание того же дня: «поля больше,
 * чем нужно». Ширина поля — по самому длинному значению, замеренному
 * шрифтом экрана (холст по живому полю), а не на глаз: урок «Вписать
 * адре». Под широкими колонками — судьёй и человеком — поле стоит
 * по центру, как и всё содержимое колонки, а не растягивается во всю её
 * ширину: там колонку распирают цитата судьи и три кнопки решения.
 *
 * **На телефоне строка фильтров уезжает в прокрутку вместе с таблицей** —
 * как у доноров: фильтр остаётся под своей колонкой.
 */

import {
  Alert,
  Button,
  Combobox,
  CheckIcon,
  Group,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
} from '@mantine/core';
import type { ComboboxItem, SelectProps } from '@mantine/core';
import type { ReactNode } from 'react';

import {
  SELECTION_ANSWERS,
  SELECTION_HUMAN,
  SELECTION_JUDGES,
  SELECTION_THRESHOLDS,
} from '../api/labels';
import type { HumanIntent, SelectionCard } from '../api/types';
import { dropdownBelow } from '../theme';
import { SelectionRow } from './SelectionRow';
import { answersOn, HUMAN_KEYS, JUDGE_KEYS, thresholdsOn } from './selectionFilters';
import type { Emptiness, SelectionFilters } from './selectionFilters';

/** Ширины полей фильтра — по самому длинному значению, замеренному шрифтом
 *  экрана 26.09.2026 (холст по живому полю, 12 px): «до Ahrefs не дошёл» —
 *  114 px, «судья не смотрел» — 104, «берёт бесплатно» — 100, «разошёлся
 *  с судьёй» — 119. Список под полем — по его ширине, и пункту в нём нужно
 *  на 8 px больше, чем полю: у поля к тексту 40 px (поле слева, шеврон,
 *  кромка), у пункта — 48 (место под галочку, зазор, поля пункта и списка).
 *  По полю «до Ahrefs не дошёл» в списке рвалось на две строки. Поэтому
 *  ширина — по пункту списка, плюс два-три пикселя запаса на другой шрифт.
 *  У судьи список по своему содержимому (`WIDE_LIST`), и поле — по значению. */
const FIELD = {
  thresholds: '10.25rem',
  judge: '9.125rem',
  answer: '9.375rem',
  human: '10.625rem',
} as const;

/** Колонки слева направо. Домену — остаток. Остальные — по самому широкому,
 *  что в них бывает, плюс поля ячейки по 10 px: пороги и ответ донора — по
 *  своему полю фильтра (значки «не проверен» — 98 px, «берёт бесплатно» —
 *  123, заголовок «Донор ответил» — 120 — уже поля); судья — по цитате
 *  (не шире 260 px), ряд значков переносится целыми значками; человек —
 *  по трём кнопкам решения в ряд, 314 px.
 *
 *  Остаток домену на 1440 px — 171 px текста: из 1 065 доменов базы
 *  разработки переносятся 87, самые длинные — до 232 px (замер холстом
 *  26.09.2026). Судье поэтому — ровно под цитату, а не с запасом: при
 *  прежних 288 px переносились бы 195 доменов. */
const COLUMNS: { title: string; width?: string }[] = [
  { title: 'Домен' },
  { title: 'Пороги', width: '11.5rem' },
  { title: 'Судья', width: '17.75rem' },
  { title: 'Донор ответил', width: '10.625rem' },
  { title: 'Человек', width: '21rem' },
];

/** Уже этого таблица не сжимается и уезжает в прокрутку: колонкам — их
 *  ширины (974 px), домену — поля ячейки и поле поиска, в котором подсказка
 *  «Домен или причина» (117 px) не режется. */
export const TABLE_MIN_WIDTH = 1140;

/** Значение «не сужать» у всех фильтров строки — одним словом, как у доноров. */
const ALL = 'all';

/** Класс галочки выбранного пункта из списков Mantine: у пункта со своей
 *  разметкой галочка та же — размером и цветом, — что у остальных списков. */
const CHECK_ICON = (Combobox.classes as Record<string, string | undefined>)
  .optionsDropdownCheckIcon;

interface FilterProps<T extends string> {
  label: string;
  width: string;
  value: T | null;
  choices: T[];
  titleOf: (value: T) => string;
  onChange: (value: T | null) => void;
  /** Пункт со своей разметкой — и тогда список по своему содержимому. */
  renderOption?: NonNullable<SelectProps['renderOption']>;
}

/** Список, у пунктов которого есть объяснение, — по ширине содержимого,
 *  а не поля: в ширине поля (146 px) «выдача и главная сказали одно» рвалось
 *  на три строки, и пять пунктов вставали столбом в полэкрана. Левый край —
 *  по полю, как у остальных списков: он не уезжает и не бывает уже поля. */
const WIDE_LIST = { ...dropdownBelow, width: 'max-content' } as const;

/** Выбор под колонкой: «все» и значения колонки. Список — по ширине поля
 *  (умолчание темы), поле — по центру ячейки: `Select` блочный, текстовое
 *  выравнивание колонки его не двигает. */
function ColumnFilter<T extends string>({
  label,
  width,
  value,
  choices,
  titleOf,
  onChange,
  renderOption,
}: FilterProps<T>) {
  return (
    <Group justify="center">
      <Select
        size="xs"
        w={width}
        aria-label={label}
        allowDeselect={false}
        value={value ?? ALL}
        onChange={(picked) => onChange(choices.find((choice) => choice === picked) ?? null)}
        data={[
          { value: ALL, label: 'все' },
          ...choices.map((choice) => ({ value: choice, label: titleOf(choice) })),
        ]}
        {...(renderOption !== undefined ? { renderOption, comboboxProps: WIDE_LIST } : {})}
      />
    </Group>
  );
}

/** Пункт фильтра судьи: слово значка и что за ним стоит — чтобы смысл
 *  читался в самом списке, а не в подсказке по наведению (замечание
 *  26.09.2026: «Кто решил у судьи» было непонятно). Галочка — тем же
 *  значком и классом, что у остальных списков: пункты стоят в одну колонку. */
function judgeOption({ option, checked }: { option: ComboboxItem; checked?: boolean }) {
  const hint = JUDGE_KEYS.find((key) => key === option.value);
  const said = hint === undefined ? undefined : SELECTION_JUDGES[hint].hint;
  return (
    <>
      {checked === true && <CheckIcon className={CHECK_ICON} />}
      <span>
        <Text component="span" size="xs" fw={500} display="block">
          {option.label}
        </Text>
        {said !== undefined && (
          <Text component="span" size="xs" c="dimmed" display="block" className="optionHint">
            {said}
          </Text>
        )}
      </span>
    </>
  );
}

export interface FilterRowProps {
  filters: SelectionFilters;
  /** Поиск — как набран сейчас: в адрес он уходит после паузы. */
  search: string;
  onSearch: (value: string) => void;
  onFilter: (patch: Partial<SelectionFilters>) => void;
}

function FilterRow({ filters, search, onSearch, onFilter }: FilterRowProps) {
  const thresholds = thresholdsOn(filters.tab);
  return (
    <Table.Tr className="filterRow">
      <Table.Th>
        {/* Без значка лупы, как у доноров: что это поиск, говорит подсказка. */}
        <TextInput
          size="xs"
          placeholder="Домен или причина"
          aria-label="Поиск по домену или причине"
          value={search}
          onChange={(event) => onSearch(event.currentTarget.value)}
        />
      </Table.Th>
      <Table.Th>
        {/* У принятых вердикт порогов один — «подходит», по определению
            вкладки: выбирать не из чего, и поля нет. */}
        {thresholds.length > 0 && (
          <ColumnFilter
            label="Вердикт порогов"
            width={FIELD.thresholds}
            value={filters.thresholds}
            choices={thresholds}
            titleOf={(value) => SELECTION_THRESHOLDS[value]}
            onChange={(value) => onFilter({ thresholds: value })}
          />
        )}
      </Table.Th>
      <Table.Th>
        <ColumnFilter
          label="Кто вынес вердикт"
          width={FIELD.judge}
          value={filters.judge}
          choices={JUDGE_KEYS}
          titleOf={(value) => SELECTION_JUDGES[value].title}
          onChange={(value) => onFilter({ judge: value })}
          renderOption={judgeOption}
        />
      </Table.Th>
      <Table.Th>
        <ColumnFilter
          label="Ответ донора"
          width={FIELD.answer}
          value={filters.answer}
          choices={answersOn(filters.tab)}
          titleOf={(value) => SELECTION_ANSWERS[value]}
          onChange={(value) => onFilter({ answer: value })}
        />
      </Table.Th>
      <Table.Th>
        <ColumnFilter
          label="Решение человека"
          width={FIELD.human}
          value={filters.human}
          choices={HUMAN_KEYS}
          titleOf={(value) => SELECTION_HUMAN[value]}
          onChange={(value) => onFilter({ human: value })}
        />
      </Table.Th>
    </Table.Tr>
  );
}

/** Одна строка на всю ширину: пусто или отказ. Фильтры над ней остаются —
 *  поправить условие можно тут же, не возвращаясь. */
function WholeRow({ children }: { children: ReactNode }) {
  return (
    <Table.Tr className="wholeRow">
      <Table.Td colSpan={COLUMNS.length}>{children}</Table.Td>
    </Table.Tr>
  );
}

interface Props extends FilterRowProps {
  rows: SelectionCard[];
  /** Строки прежней вкладки или фильтра — ждут замены: решать по ним нельзя. */
  stale: boolean;
  empty: Emptiness | null;
  refusal: string | null;
  mayDecide: boolean;
  /** Домен, решение по которому сейчас уходит на сервер. */
  deciding: number | null;
  onDecide: (row: SelectionCard, intent: HumanIntent | null) => void;
  onReset: () => void;
}

export function SelectionTable({
  rows,
  stale,
  empty,
  refusal,
  mayDecide,
  deciding,
  onDecide,
  onReset,
  ...filters
}: Props) {
  return (
    <Table.ScrollContainer minWidth={TABLE_MIN_WIDTH} type="native" className="scrollSlim">
      <Table
        className="dataTable filteredTable selectionTable"
        layout="fixed"
        verticalSpacing="sm"
        horizontalSpacing="xs"
      >
        <colgroup>
          {COLUMNS.map((column) => (
            <col key={column.title} style={column.width ? { width: column.width } : undefined} />
          ))}
        </colgroup>
        <Table.Thead>
          <Table.Tr>
            {COLUMNS.map((column) => (
              <Table.Th key={column.title}>{column.title}</Table.Th>
            ))}
          </Table.Tr>
          <FilterRow {...filters} />
        </Table.Thead>
        <Table.Tbody
          className="staleRows"
          data-stale={stale || undefined}
          aria-busy={stale || undefined}
        >
          {refusal !== null && (
            <WholeRow>
              <Alert color="red" title="Отбор не загрузился">
                {refusal}
              </Alert>
            </WholeRow>
          )}
          {refusal === null && empty !== null && (
            <WholeRow>
              <Stack gap={6} align="flex-start" py="sm">
                <Text size="sm" fw={500}>
                  {empty.title}
                </Text>
                <Text size="sm" c="dimmed">
                  {empty.detail}
                </Text>
                {empty.resettable && (
                  <Button variant="subtle" size="compact-sm" className="press" onClick={onReset}>
                    Сбросить фильтры
                  </Button>
                )}
              </Stack>
            </WholeRow>
          )}
          {refusal === null &&
            rows.map((row) => (
              <SelectionRow
                key={row.domain_id}
                row={row}
                mayDecide={mayDecide}
                busy={stale || deciding === row.domain_id}
                onDecide={onDecide}
              />
            ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
