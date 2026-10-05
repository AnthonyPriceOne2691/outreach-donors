/**
 * Продажи: гипотезы, лиды и дорога к загрузке базы.
 *
 * **Экран показывает то, что записали загрузка и очистка, и ничего не решает
 * сам.** Состояние лида и причина отказа — те, что в базе; экран называет их
 * словами и даёт по ним сузить таблицу. Очистка идёт командой, здесь виден её
 * след: сколько готово к письмам, сколько и почему отсеяно.
 *
 * **Сводка — по всем лидам, таблица — под фильтрами** (как у отбора). Плитки
 * считаются по гипотезам: один запрос даёт и вкладку гипотез, и числа сверху,
 * и список для фильтра. Фильтры, вкладка и страница живут в адресе
 * (`leadFilters.ts`): `?state=rejected&reason=duplicate` переживает перезагрузку.
 *
 * **Смена фильтра — не перезагрузка.** Прежние строки стоят приглушёнными,
 * пока едут новые; отказ сервера на новом фильтре встаёт строкой в таблицу,
 * а сводка и фильтры остаются — условие поправляют тут же.
 */

import {
  Button,
  Card,
  Group,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo } from 'react';
import type { ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { listHypotheses } from '../api/sales';
import { LEAD_STATES } from '../api/salesLabels';
import type { HypothesisCard, LeadState } from '../api/salesTypes';
import { Metric } from '../components/Metric';
import { useTyped } from '../donors/useTyped';
import { formatNumber } from '../format';
import { HypothesesTable } from './HypothesesTable';
import { useKb } from './kbData';
import { KbPane } from './KbPane';
import {
  LEAD_STATE_KEYS,
  NO_LEAD_FILTERS,
  readLeadFilters,
  SALES_TAB_KEYS,
  SALES_TABS,
  writeLeadFilters,
} from './leadFilters';
import type { LeadFilters, SalesTab } from './leadFilters';
import { LeadsPane, pageBack, useLeads } from './LeadsPane';
import { Pending } from './Pending';
import { SenderPane } from './SenderPane';

export const HYPOTHESES_QUERY_KEY = ['sales', 'hypotheses'] as const;

/** Набранный поиск совпадает с адресом без пробелов по краям. */
const sameSearch = (draft: string, committed: string) => draft.trim() === committed;

/** Плитки сводки: состояние → подпись. Порядок — порядок пути лида. */
const TILES: Record<LeadState, string> = { new: 'Новые', ready: 'Готовы', rejected: 'Отклонены' };

type Totals = Record<LeadState, number> & { total: number };

/** Сводка по всем гипотезам — она же числа вкладок и плиток. */
function summarize(rows: HypothesisCard[]): Totals {
  const totals: Totals = { new: 0, ready: 0, rejected: 0, total: 0 };
  for (const row of rows) {
    for (const state of LEAD_STATE_KEYS) totals[state] += row.leads[state];
    totals.total += row.total;
  }
  return totals;
}

function Summary({ totals, hypotheses }: { totals: Totals; hypotheses: number }) {
  return (
    <Card className="glassPanel" p="xl">
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" wrap="wrap" gap="md">
          {/* Основа в 20rem: на телефоне кнопка уходит под текст, а не сжимает его
              в узкую колонку (снимок 390 px, 04.10.2026). */}
          <Stack gap={6} style={{ flex: '1 1 20rem', minWidth: 0 }}>
            <Title order={3}>Продажи</Title>
            <Text size="sm" c="dimmed" maw={720}>
              Лиды попадают сюда из файла или Google-таблицы, проходят очистку — дубли, стоп-листы,
              почта домена, проверка адреса — и готовыми уходят в письма. У каждого отсеянного
              названа причина: кодом, по которому фильтр, и словами, что именно нашлось.
            </Text>
          </Stack>
          <Button component={Link} to="/sales/import" className="press">
            Загрузить базу
          </Button>
        </Group>
        <SimpleGrid cols={{ base: 2, sm: 3, lg: 5 }} spacing="sm">
          <Metric title="Лидов" value={formatNumber(totals.total)} />
          {LEAD_STATE_KEYS.map((state) => (
            <Metric
              key={state}
              title={TILES[state]}
              value={formatNumber(totals[state])}
              color={state === 'ready' && totals.ready > 0 ? LEAD_STATES.ready.color : undefined}
            />
          ))}
          <Metric title="Гипотез" value={formatNumber(hypotheses)} />
        </SimpleGrid>
      </Stack>
    </Card>
  );
}

interface TabsProps {
  tab: SalesTab;
  /** Числа на вкладках: лидов, гипотез, записей базы. Нет числа — одно имя:
   *  у отправителя считать нечего, а база ещё едет. */
  counts: Partial<Record<SalesTab, number | undefined>>;
  onTab: (tab: SalesTab) => void;
}

function tabLabel(key: SalesTab, count: number | undefined): string {
  return count === undefined ? SALES_TABS[key] : `${SALES_TABS[key]} — ${formatNumber(count)}`;
}

/** Вкладки раздела с числами. На узком окне — столбиком: вкладки с числами
 *  в ряд резались до первых букв. */
function SalesTabs({ tab, counts, onTab }: TabsProps) {
  const narrow = useMediaQuery('(max-width: 36em)');
  return (
    <SegmentedControl
      orientation={narrow ? 'vertical' : 'horizontal'}
      fullWidth={narrow}
      aria-label="Вкладки продаж"
      value={tab}
      onChange={(value) => {
        const next = SALES_TAB_KEYS.find((key) => key === value);
        if (next !== undefined && next !== tab) onTab(next);
      }}
      data={SALES_TAB_KEYS.map((key) => ({ value: key, label: tabLabel(key, counts[key]) }))}
    />
  );
}

export function SalesPage() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => readLeadFilters(params), [params]);

  // Смена вкладки или фильтра — замена записи в истории и первая страница;
  // страница — новая запись: «назад» ведёт по страницам, а не по буквам поиска.
  const apply = useCallback(
    (patch: Partial<LeadFilters>) =>
      setParams((current) => writeLeadFilters({ ...readLeadFilters(current), ...patch, page: 1 }), {
        replace: true,
      }),
    [setParams],
  );
  const turn = useCallback(
    (page: number) =>
      setParams((current) => writeLeadFilters({ ...readLeadFilters(current), page })),
    [setParams],
  );
  const [search, setSearch] = useTyped(
    filters.search,
    (value) => apply({ search: value.trim() }),
    sameSearch,
  );

  // Списки гипотез и лидов уходят вместе, а не друг за другом.
  const hypotheses = useQuery({ queryKey: HYPOTHESES_QUERY_KEY, queryFn: listHypotheses });
  const leads = useLeads(filters);
  // База знаний — ради числа на вкладке: пустую базу видно и со вкладки лидов.
  const kb = useKb();

  // Страница из старой ссылки за концом — последняя настоящая, а не пустота.
  const back = pageBack(leads.answer, filters.page);
  useEffect(() => {
    if (back !== null) {
      setParams((current) => writeLeadFilters({ ...readLeadFilters(current), page: back }), {
        replace: true,
      });
    }
  }, [back, setParams]);

  if (hypotheses.data === undefined) return <Pending error={hypotheses.error} />;

  const known = hypotheses.data.rows;
  const totals = summarize(known);
  // Другая вкладка и «Сбросить фильтры» — чистый адрес: набранный поиск уходит с ним.
  const reset = (tab: SalesTab) => {
    setSearch('');
    setParams(writeLeadFilters({ ...NO_LEAD_FILTERS, tab }), { replace: true });
  };

  // Вкладка → её содержимое: таблицей, а не цепочкой условий.
  const bodies: Record<SalesTab, () => ReactNode> = {
    leads: () => (
      <LeadsPane
        leads={leads}
        total={totals.total}
        filters={filters}
        search={search}
        onSearch={setSearch}
        onFilter={apply}
        hypotheses={known}
        onTurn={turn}
        onReset={() => reset('leads')}
      />
    ),
    hypotheses: () => <HypothesesTable rows={known} />,
    kb: () => <KbPane />,
    sender: () => <SenderPane />,
  };

  return (
    <Stack gap="lg">
      <Summary totals={totals} hypotheses={known.length} />

      <Card className="glassPanel" p="md">
        <Stack gap="sm">
          <SalesTabs
            tab={filters.tab}
            counts={{ leads: totals.total, hypotheses: known.length, kb: kb.data?.total }}
            onTab={reset}
          />
          {bodies[filters.tab]()}
        </Stack>
      </Card>
    </Stack>
  );
}
