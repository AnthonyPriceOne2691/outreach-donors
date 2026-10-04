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
  Alert,
  Button,
  Card,
  Group,
  Loader,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { listHypotheses, listLeads } from '../api/sales';
import { LEAD_STATES } from '../api/salesLabels';
import type { HypothesisCard, LeadsView, LeadState } from '../api/salesTypes';
import { Metric } from '../components/Metric';
import { PageSwitch } from '../components/PageSwitch';
import { useTyped } from '../donors/useTyped';
import { formatNumber } from '../format';
import { HypothesesTable } from './HypothesesTable';
import {
  emptinessOf,
  LEAD_STATE_KEYS,
  NO_LEAD_FILTERS,
  queryOf,
  readLeadFilters,
  SALES_TAB_KEYS,
  SALES_TABS,
  writeLeadFilters,
} from './leadFilters';
import type { LeadFilters } from './leadFilters';
import { LeadsTable } from './LeadsTable';

export const HYPOTHESES_QUERY_KEY = ['sales', 'hypotheses'] as const;
const LEADS_QUERY_KEY = ['sales', 'leads'] as const;

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

export function SalesPage() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => readLeadFilters(params), [params]);
  // На узком окне две вкладки с числами в ряд резались до первых букв.
  const narrow = useMediaQuery('(max-width: 36em)');

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

  const hypotheses = useQuery({ queryKey: HYPOTHESES_QUERY_KEY, queryFn: listHypotheses });
  const onLeads = filters.tab === 'leads';
  const leads = useQuery({
    queryKey: [
      ...LEADS_QUERY_KEY,
      filters.state,
      filters.reason,
      filters.hypothesis,
      filters.search,
      filters.page,
    ],
    queryFn: () => listLeads(queryOf(filters)),
    placeholderData: keepPreviousData,
    // На вкладке гипотез таблицы лидов нет — и запроса за ней нет.
    enabled: onLeads,
  });

  // Отказ сервера на новом фильтре не должен убирать таблицу вместе с
  // фильтрами: последняя пришедшая страница даёт фильтрам список причин.
  const [lastLeads, setLastLeads] = useState<LeadsView | undefined>(undefined);
  useEffect(() => {
    if (leads.data !== undefined) setLastLeads(leads.data);
  }, [leads.data]);
  const table = leads.data ?? lastLeads;
  const answer = leads.isPlaceholderData ? undefined : leads.data;
  const pages =
    leads.data === undefined ? 1 : Math.max(1, Math.ceil(leads.data.total / leads.data.limit));

  // Страница из старой ссылки за концом — последняя настоящая, а не пустота.
  useEffect(() => {
    if (answer !== undefined && answer.rows.length === 0 && answer.total > 0) {
      const last = Math.max(1, Math.ceil(answer.total / answer.limit));
      if (filters.page > last) {
        setParams((current) => writeLeadFilters({ ...readLeadFilters(current), page: last }), {
          replace: true,
        });
      }
    }
  }, [answer, filters.page, setParams]);

  if (hypotheses.data === undefined) {
    return hypotheses.error ? (
      <Alert color="red" title="Раздел продаж не загрузился" m="md">
        {refusalOf(hypotheses.error)}
      </Alert>
    ) : (
      <Loader aria-label="Загружаем раздел продаж" m="md" />
    );
  }

  const known = hypotheses.data.rows;
  const totals = summarize(known);
  const nameOf = (id: number) => known.find((row) => row.id === id)?.name;
  const tabCounts: Record<string, number> = { leads: totals.total, hypotheses: known.length };
  const stale = leads.isPlaceholderData;
  const refusal = leads.data === undefined && leads.error ? refusalOf(leads.error) : null;
  const rows = leads.data?.rows ?? [];

  return (
    <Stack gap="lg">
      <Summary totals={totals} hypotheses={known.length} />

      <Card className="glassPanel" p="md">
        <Stack gap="sm">
          <SegmentedControl
            orientation={narrow ? 'vertical' : 'horizontal'}
            fullWidth={narrow}
            aria-label="Вкладки продаж"
            value={filters.tab}
            onChange={(value) => {
              const tab = SALES_TAB_KEYS.find((key) => key === value);
              if (tab !== undefined && tab !== filters.tab) {
                setSearch('');
                setParams(writeLeadFilters({ ...NO_LEAD_FILTERS, tab }), { replace: true });
              }
            }}
            data={SALES_TAB_KEYS.map((key) => ({
              value: key,
              label: `${SALES_TABS[key]} — ${formatNumber(tabCounts[key] ?? 0)}`,
            }))}
          />

          {!onLeads && <HypothesesTable rows={known} />}

          {onLeads &&
            table === undefined &&
            // Первого ответа ещё нет — или не будет: крутилка только до него.
            (refusal !== null ? (
              <Alert color="red" title="Лиды не загрузились">
                {refusal}
              </Alert>
            ) : (
              <Loader aria-label="Загружаем лидов" m="md" />
            ))}

          {onLeads && table !== undefined && (
            <>
              <LeadsTable
                rows={rows}
                stale={stale}
                refusal={refusal}
                empty={
                  answer !== undefined && rows.length === 0
                    ? emptinessOf(filters, totals.total, nameOf)
                    : null
                }
                filters={filters}
                search={search}
                onSearch={setSearch}
                onFilter={apply}
                hypotheses={known}
                reasons={Object.keys(table.reasons)}
                onReset={() => {
                  setSearch('');
                  setParams(writeLeadFilters(NO_LEAD_FILTERS), { replace: true });
                }}
              />
              <PageSwitch
                label="Страницы лидов"
                page={filters.page}
                pages={pages}
                onChange={turn}
                pt="xs"
              />
            </>
          )}
        </Stack>
      </Card>
    </Stack>
  );
}
