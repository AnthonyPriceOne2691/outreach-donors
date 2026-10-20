/**
 * Над таблицей доноров — решение человека, цена и порядок строк (сверка ТЗ 12.10.2026).
 *
 * «Решение» говорит, кто на экране: доноры — как было (решение 26.09.2026), отклонённые,
 * без решения или все проверенные сайты — таблица ТЗ «со всеми сайтами, статусом
 * и объяснением, по какому порогу сайт отсеян». «Цена» сужает по цене: с ценой, свежая,
 * устарела, без цены; своей колонки у цены нет — она строкой под доменом, и фильтр
 * стоит здесь. «Порядок» — по DR, трафику, цене и дате цены: ТЗ просит «фильтры,
 * сортировку, выгрузку», а порядок был один. Поля — в шапке, рядом с выгрузкой: её файл
 * отвечает теми же строками в том же порядке.
 */

import { Group, Select, Text } from '@mantine/core';
import type { ReactNode } from 'react';

import type { PriceState, ReviewScope } from '../api/donorPrice';
import type { TopicCount } from '../api/topics';
import { InfoHint } from '../components/InfoHint';
import { formatNumber } from '../format';
import { anyTopic } from '../topics/topicRows';
import { TopicFilter } from '../topics/TopicFilter';
import { PRICES, REVIEWS, SORTS } from './donorFilters';
import type { DonorFilters } from './donorFilters';
import { DONOR_SORTS, PRICE_STATES, REVIEW_SCOPES } from './priceWords';

interface Props {
  filters: DonorFilters;
  /** Решение → сколько записей во всей базе; `null` — сводка ещё не пришла. */
  reviews: Partial<Record<ReviewScope, number>> | null;
  /** Цена → сколько доноров экрана; `null` — сводка ещё не пришла. */
  prices: Partial<Record<PriceState, number>> | null;
  /** Срок годности цены, дней, — числом сервера; `null` — сводка не пришла. */
  priceTtlDays: number | null;
  /** Пункты «Тематики» со счётчиками; `null` — сводка не пришла. Тем нет — фильтра нет. */
  topics: readonly TopicCount[] | null;
  onFilter: (patch: Partial<DonorFilters>) => void;
}

/** Подпись поля — словом перед ним: значение «доноры» без неё читалось бы загадкой. */
function Labeled({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <Group gap={6} wrap="nowrap">
      <Text size="sm" c="dimmed" component="span">
        {label}
      </Text>
      {children}
    </Group>
  );
}

/** «Значение · число» в пункте списка; числа нет — только значение. */
function counted(label: string, count: number | undefined): ReactNode {
  return <span>{count === undefined ? label : `${label} · ${formatNumber(count)}`}</span>;
}

/** Когда цена устарела — у фильтра цены. Срок — числом сервера, своей копии у экрана нет. */
function PriceHint({ days }: { days: number | null }) {
  const age = days === null ? 'раньше срока годности' : `больше ${formatNumber(days)} дней назад`;
  return (
    <InfoHint name="Когда цена устарела" width={300}>
      Цена устарела — названа {age}: сначала перезапрос у донора, оффер Этапа 2 на ней не строится.
      Валюта — как назвал донор: конвертации нет, и порядок «по цене» сравнивает числа.
    </InfoHint>
  );
}

export function DonorsScope({ filters, reviews, prices, priceTtlDays, topics, onFilter }: Props) {
  return (
    <Group gap="md" wrap="wrap">
      <Labeled label="Решение">
        <Select
          size="sm"
          w={172}
          aria-label="Решение человека"
          allowDeselect={false}
          value={filters.review}
          onChange={(value) =>
            onFilter({ review: REVIEWS.find((one) => one === value) ?? 'accepted' })
          }
          data={REVIEWS.map((one) => ({ value: one, label: REVIEW_SCOPES[one].title }))}
          renderOption={({ option }) =>
            counted(option.label, reviews?.[option.value as ReviewScope])
          }
        />
      </Labeled>
      <Labeled
        label={
          <Group gap={2} wrap="nowrap" component="span">
            Цена
            <PriceHint days={priceTtlDays} />
          </Group>
        }
      >
        <Select
          size="sm"
          w={128}
          aria-label="Цена"
          allowDeselect={false}
          value={filters.price ?? 'all'}
          onChange={(value) => onFilter({ price: PRICES.find((one) => one === value) ?? null })}
          data={[
            { value: 'all', label: 'все' },
            ...PRICES.map((one) => ({ value: one, label: PRICE_STATES[one].title })),
          ]}
          renderOption={({ option }) =>
            counted(
              option.label,
              option.value === 'all' ? undefined : prices?.[option.value as PriceState],
            )
          }
        />
      </Labeled>
      <Labeled label="Порядок">
        <Select
          size="sm"
          w={164}
          aria-label="Порядок строк"
          allowDeselect={false}
          value={filters.sort}
          onChange={(value) => onFilter({ sort: SORTS.find((one) => one === value) ?? 'dr' })}
          data={SORTS.map((one) => ({ value: one, label: DONOR_SORTS[one] }))}
        />
      </Labeled>
      {(anyTopic(topics ?? []) || filters.topic !== null) && (
        <TopicFilter
          value={filters.topic}
          items={topics}
          onChange={(topic) => onFilter({ topic })}
        />
      )}
    </Group>
  );
}
