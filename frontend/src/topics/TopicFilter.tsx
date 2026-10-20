/**
 * Фильтр «Тематика» — словом и полем над таблицей, как «Цена»: своей колонки у темы нет,
 * она строкой под доменом (`TopicLine`).
 *
 * Пункты — темы, что есть у строк экрана, частые сверху, со счётчиками в списке, а в поле —
 * только название: по нему ищут («фин» находит «Финансы · 12»). Счётчики считает тот же
 * источник, что строки: у «Доноров» — сервер по всем донорам экрана, у очереди прогона —
 * её строки. Тема из адреса, которой у строк нет, — всё равно пункт: иначе поле показало бы
 * «все», а таблица была бы сужена до неё.
 */

import { Group, Select, Text } from '@mantine/core';

import { NOT_DEFINED } from '../api/topics';
import type { TopicCount } from '../api/topics';
import { InfoHint } from '../components/InfoHint';
import { formatNumber } from '../format';
import { NOT_DEFINED_LABEL } from './topicRows';

/** Пункт «все» — не сужать. */
const ALL = 'all';

/** Поле — по самому длинному пункту списка, а не по значению в поле (замер 20.10.2026):
 *  «Промышленность · 1 234» — 169 px шрифтом экрана, у пункта 32 px слева (поле и место под
 *  галочку) и 10 справа, список на 10 px уже поля. По полю в 176 px пункт рвался на
 *  «Промышленность» и «· 1», в 220 — на пиксель. */
export const FILTER_WIDTH = 228;

function TopicHint() {
  return (
    <InfoHint name="Что такое тематика" width={300}>
      Тема сайта из общего списка: её определяет модель по странице из выдачи и главной сайта. У
      сайта бывает и вторая тема — фильтр находит его и по ней. «Не определена» — тему ещё не
      определяли или модель не поняла её по тексту.
    </InfoHint>
  );
}

interface Props {
  /** Выбранная тема — код; `none` — не определена; `null` — все. */
  value: string | null;
  /** Пункты со счётчиками; `null` — ещё не пришли: подписи без чисел, а не с нулями. */
  items: readonly TopicCount[] | null;
  onChange: (code: string | null) => void;
}

export function TopicFilter({ value, items, onChange }: Props) {
  const known = items ?? [];
  const options = known.map((item) => ({ value: item.code, label: item.label }));
  if (value !== null && !known.some((item) => item.code === value)) {
    options.push({ value, label: value === NOT_DEFINED ? NOT_DEFINED_LABEL : value });
  }
  const counts = new Map(known.map((item) => [item.code, item.count]));
  return (
    <Group gap={6} wrap="nowrap">
      <Group gap={2} wrap="nowrap">
        <Text size="sm" c="dimmed" component="span">
          Тематика
        </Text>
        <TopicHint />
      </Group>
      <Select
        size="sm"
        w={FILTER_WIDTH}
        aria-label="Тематика"
        searchable
        allowDeselect={false}
        nothingFoundMessage="Такой тематики у сайтов нет"
        value={value ?? ALL}
        onChange={(next) => onChange(next === null || next === ALL ? null : next)}
        data={[{ value: ALL, label: 'все' }, ...options]}
        renderOption={({ option }) => {
          const count = counts.get(option.value);
          return (
            <span>
              {count === undefined ? option.label : `${option.label} · ${formatNumber(count)}`}
            </span>
          );
        }}
      />
    </Group>
  );
}
