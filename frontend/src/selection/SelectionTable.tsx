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
 *
 * **Домену — своя ширина, а не остаток, и все колонки по центру**
 * (замечание 28.09.2026: «колонку с доменами уже, она сильно широкая, и поле
 * фильтра над ней тоже уже; центрируй все колонки»). На широком экране
 * домену доставалось 312 px, а полю поиска — 286.
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
import type { GoneRow } from './useDecisions';

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
  /** Поиск — по подсказке «Домен или причина» (117 px шрифтом экрана) и полям
   *  поля; длинный домен в нём прокручивается, как в любом поле ввода. */
  search: '10rem',
  thresholds: '10.25rem',
  judge: '9.125rem',
  answer: '9.375rem',
  human: '10.625rem',
} as const;

interface Column {
  title: string;
  /** Не задана — колонке достаётся остаток ширины таблицы. */
  width?: string;
}

/** Колонки слева направо — так, чтобы таблица вставала целиком в самое узкое
 *  место, где её смотрят: панель 948 px на окне 1280 с раскрытым меню (на 1440 —
 *  1108; замер 10.10.2026). Колонки по самому широкому содержимому давали 1 166 px,
 *  и «Отклонены» и «К разбору» уезжали в прокрутку уже на 1440, а «Не продаёт
 *  места» обрезался краем панели (проверка QA 10.10.2026).
 *
 *  Колонка — не уже поля фильтра над ней и полей ячейки по 10 px: домен — 180
 *  (поиск 160; не влез домен — многоточие, `SelectionRow`), пороги — 184, ответ
 *  донора — 170, человек — 190. Замер содержимого в браузере: три кнопки решения
 *  в ряд — 316 px, в два ряда — 185 («Площадка» и «Продаёт своё»), значки судьи
 *  в ряд — 235, DR с отступом в строке домена — 43, домен — ~8 px на знак.
 *
 *  **У принятых колонки «Пороги» нет**: вердикт порогов у них один — «подходит»,
 *  по определению вкладки (аудит экранов 09.10.2026). Запас широкого окна браузер
 *  делит между колонками по их ширинам, и ширины подобраны под два окна (проверка
 *  прода 10.10.2026): на 1280 домену было 181 px, и обычный домен в 15 знаков
 *  резался многоточием, а кнопки стояли в ряд в колонке 337 px. Теперь на 1280
 *  домену — 227 px (домен в 20 знаков и DR в строку), кнопки встают в два ряда:
 *  строку это не растит — её высоту держит цитата судьи, у принятых она почти
 *  всегда есть. От 1440 «Человеку» — 340 px, и кнопки снова в ряд. */
const WITHOUT_THRESHOLDS: Column[] = [
  { title: 'Домен', width: '14.125rem' },
  { title: 'Судья', width: '16.125rem' },
  { title: 'Донор ответил', width: '10.625rem' },
  { title: 'Человек', width: '18.125rem' },
];

/** **С «Порогами» кнопки встают в два ряда**: строка там и так выше — значок
 *  порогов и причина под ним, — и два ряда кнопок её не растят. Домену — те же
 *  192 px, что и до правки. Судье — весь остаток: на 1280 его 194 px, и значки
 *  переносятся, а на 1440 — 354, и строки не выше, чем были до правки. Делить
 *  запас по долям значило бы оставить судье ~240 px и на 1440 — строки с цитатой
 *  вырастали со 103 до 144 px. */
const WITH_THRESHOLDS: Column[] = [
  { title: 'Домен', width: '12rem' },
  { title: 'Пороги', width: '11.5rem' },
  { title: 'Судья' },
  { title: 'Донор ответил', width: '10.625rem' },
  { title: 'Человек', width: '13rem' },
];

/** Уже этого таблица не сжимается и уезжает в прокрутку: сумма колонок принятых —
 *  944 px, на 4 меньше панели на 1280; судье с «Порогами» при ней остаётся 190. */
export const TABLE_MIN_WIDTH = 944;

/** Колонки вкладки: с «Порогами» — у всех, кроме принятых. */
function columnsOn(tab: SelectionFilters['tab']): Column[] {
  return thresholdsOn(tab).length > 0 ? WITH_THRESHOLDS : WITHOUT_THRESHOLDS;
}

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
        {/* Без значка лупы, как у доноров: что это поиск, говорит подсказка.
            Поле — по центру колонки, как остальные фильтры: `TextInput`
            блочный, и выравнивание колонки его не двигает. */}
        <Group justify="center">
          <TextInput
            size="xs"
            w={FIELD.search}
            placeholder="Домен или причина"
            aria-label="Поиск по домену или причине"
            value={search}
            onChange={(event) => onSearch(event.currentTarget.value)}
          />
        </Group>
      </Table.Th>
      {/* У принятых вердикт порогов один — «подходит», по определению вкладки:
          колонки нет вовсе (`columnsOn`). */}
      {thresholds.length > 0 && (
        <Table.Th data-label="Пороги">
          <ColumnFilter
            label="Вердикт порогов"
            width={FIELD.thresholds}
            value={filters.thresholds}
            choices={thresholds}
            titleOf={(value) => SELECTION_THRESHOLDS[value]}
            onChange={(value) => onFilter({ thresholds: value })}
          />
        </Table.Th>
      )}
      <Table.Th data-label="Судья">
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
      <Table.Th data-label="Донор ответил">
        <ColumnFilter
          label="Ответ донора"
          width={FIELD.answer}
          value={filters.answer}
          choices={answersOn(filters.tab)}
          titleOf={(value) => SELECTION_ANSWERS[value]}
          onChange={(value) => onFilter({ answer: value })}
        />
      </Table.Th>
      <Table.Th data-label="Человек">
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
function WholeRow({ children, span }: { children: ReactNode; span: number }) {
  return (
    <Table.Tr className="wholeRow">
      <Table.Td colSpan={span}>{children}</Table.Td>
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
  /** Ушли решением на другую вкладку, но стоят на своём месте (`useDecisions`). */
  gone: ReadonlyMap<number, GoneRow>;
  /** Домен, решение по которому сейчас уходит на сервер. */
  deciding: number | null;
  onDecide: (row: SelectionCard, intent: HumanIntent | null) => void;
  /** «Вернуть» ушедшую строку — решение, что было до нажатия. */
  onUndo: (row: SelectionCard) => void;
  onReset: () => void;
}

export function SelectionTable({
  rows,
  stale,
  empty,
  refusal,
  mayDecide,
  gone,
  deciding,
  onDecide,
  onUndo,
  onReset,
  ...filters
}: Props) {
  const columns = columnsOn(filters.filters.tab);
  const withThresholds = columns === WITH_THRESHOLDS;
  return (
    <Table.ScrollContainer
      minWidth={TABLE_MIN_WIDTH}
      type="native"
      className="scrollSlim phoneCards"
    >
      <Table
        className="dataTable filteredTable selectionTable allCentered"
        layout="fixed"
        verticalSpacing="sm"
        horizontalSpacing="xs"
      >
        <colgroup>
          {columns.map((column) => (
            <col
              key={column.title}
              style={column.width === undefined ? undefined : { width: column.width }}
            />
          ))}
        </colgroup>
        <Table.Thead>
          <Table.Tr>
            {columns.map((column) => (
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
            <WholeRow span={columns.length}>
              <Alert color="red" title="Отбор не загрузился">
                {refusal}
              </Alert>
            </WholeRow>
          )}
          {refusal === null && empty !== null && (
            <WholeRow span={columns.length}>
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
                withThresholds={withThresholds}
                mayDecide={mayDecide}
                busy={stale || deciding === row.domain_id}
                gone={gone.get(row.domain_id) ?? null}
                onDecide={onDecide}
                onUndo={onUndo}
              />
            ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
