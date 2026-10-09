/**
 * Вкладка лидов: плитки сводки, запрос под фильтрами, таблица и страницы.
 *
 * **Плитки — только здесь** (аудит экранов 09.10.2026): пять плиток и абзац над
 * всеми вкладками ставили вкладки на телефоне в 700 px от верха, а на «Воронке»
 * вместе с её шагами выходило одиннадцать плиток. Плитка — вход в свой фильтр:
 * «Отклонены» ведёт к отклонённым. Считают они гипотезу из адреса, если она
 * выбрана, — тем же фильтром, куда ведут; «Лидов» и «Гипотез» — числа вкладок.
 *
 * **Смена фильтра — не перезагрузка.** Прежние строки стоят приглушёнными,
 * пока едут новые; отказ сервера на новом фильтре встаёт под шапкой таблицы
 * вместо строк, а фильтры остаются — условие поправляют тут же. Крутилка — только
 * до первого ответа: дальше вкладка не пропадает. Лидов нет вовсе — нет и шапки
 * с фильтрами: только объяснение.
 *
 * Запрос (`useLeads`) зовёт страница раздела, а не эта вкладка: он уходит
 * вместе со списком гипотез, а не после него — пока едут гипотезы, вкладки
 * на экране ещё нет.
 */

import { Box, Loader, SimpleGrid } from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { refusalOf } from '../api/client';
import { listLeads } from '../api/sales';
import { LEAD_STATES } from '../api/salesLabels';
import type { HypothesisCard, LeadCard, LeadState, LeadsView } from '../api/salesTypes';
import { Metric } from '../components/Metric';
import { PageSwitch } from '../components/PageSwitch';
import { formatNumber } from '../format';
import { emptinessOf, LEAD_STATE_KEYS, leadsLink, queryOf } from './leadFilters';
import type { LeadFilters } from './leadFilters';
import { LeadsEmpty, LeadsRefused, LeadsTable } from './LeadsTable';
import type { FilterRowProps } from './LeadsTable';

const LEADS_QUERY_KEY = ['sales', 'leads'] as const;

/** Плитки сводки: состояние → подпись. Порядок — порядок пути лида. */
const TILES: Record<LeadState, string> = { new: 'Новые', ready: 'Готовы', rejected: 'Отклонены' };

/** Лиды по состояниям — у выбранной гипотезы или у всех: тем же фильтром, куда ведёт плитка. */
function countsOf(hypotheses: HypothesisCard[], chosen: number | null): Record<LeadState, number> {
  const counts: Record<LeadState, number> = { new: 0, ready: 0, rejected: 0 };
  for (const row of hypotheses) {
    if (chosen !== null && row.id !== chosen) continue;
    for (const state of LEAD_STATE_KEYS) counts[state] += row.leads[state];
  }
  return counts;
}

/** Три плитки в ряд и на телефоне: подписи короткие, а ряд не встаёт стопкой над таблицей. */
function LeadTiles({
  hypotheses,
  hypothesis,
}: {
  hypotheses: HypothesisCard[];
  hypothesis: number | null;
}) {
  const counts = countsOf(hypotheses, hypothesis);
  return (
    <SimpleGrid cols={3} spacing="sm">
      {LEAD_STATE_KEYS.map((state) => (
        <Metric
          key={state}
          title={TILES[state]}
          value={formatNumber(counts[state])}
          color={state === 'ready' && counts.ready > 0 ? LEAD_STATES.ready.color : undefined}
          to={leadsLink(hypothesis, state)}
        />
      ))}
    </SimpleGrid>
  );
}

/** Последняя настоящая страница ответа — хотя бы первая, даже у пустого. */
function lastPage(view: LeadsView): number {
  return Math.max(1, Math.ceil(view.total / view.limit));
}

/** Страница из старой ссылки за концом — куда её вернуть: на последнюю
 *  настоящую, а не в пустоту. `null` — страница не за концом. */
export function pageBack(answer: LeadsView | undefined, page: number): number | null {
  if (answer === undefined || answer.rows.length > 0 || answer.total === 0) return null;
  const last = lastPage(answer);
  return page > last ? last : null;
}

export interface Leads {
  /** Что в таблице: ответ на этот вопрос, а пока он едет или после отказа —
   *  последний пришедший: он даёт фильтрам список причин. */
  table: LeadsView | undefined;
  /** Ответ именно на этот вопрос — по нему судят о пустоте. */
  answer: LeadsView | undefined;
  rows: LeadCard[];
  /** Страниц — по ответу на этот вопрос (или по прежнему, пока едет новый). */
  pages: number;
  /** Строки прежнего фильтра — ждут замены. */
  stale: boolean;
  refusal: string | null;
}

/** Лиды под фильтрами. На вкладке гипотез таблицы лидов нет — и запроса за ней нет. */
export function useLeads(filters: LeadFilters): Leads {
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
    enabled: filters.tab === 'leads',
  });
  const [last, setLast] = useState<LeadsView | undefined>(undefined);
  useEffect(() => {
    if (leads.data !== undefined) setLast(leads.data);
  }, [leads.data]);
  return {
    table: leads.data ?? last,
    answer: leads.isPlaceholderData ? undefined : leads.data,
    rows: leads.data?.rows ?? [],
    pages: leads.data === undefined ? 1 : lastPage(leads.data),
    stale: leads.isPlaceholderData,
    refusal: leads.data === undefined && leads.error ? refusalOf(leads.error) : null,
  };
}

interface Props extends Omit<FilterRowProps, 'reasons'> {
  leads: Leads;
  /** Лидов всего, по всем гипотезам: пустая таблица объясняется и им. */
  total: number;
  onTurn: (page: number) => void;
  onReset: () => void;
}

export function LeadsPane({ leads, total, onTurn, onReset, ...filterRow }: Props) {
  const { filters, hypotheses } = filterRow;
  if (total === 0) {
    // Лидов нет вовсе — ни плиток, ни шапки с фильтрами: сужать нечего, а на телефоне
    // шапка таблицы в 1 224 px уводила объяснение вбок (аудит экранов 09.10.2026).
    return (
      <Box px="md">
        <LeadsEmpty empty={emptinessOf(filters, total, () => undefined)} onReset={onReset} />
      </Box>
    );
  }
  return (
    <>
      <LeadTiles hypotheses={hypotheses} hypothesis={filters.hypothesis} />
      <LeadsBody leads={leads} total={total} onTurn={onTurn} onReset={onReset} {...filterRow} />
    </>
  );
}

/** Таблица со страницами — или крутилка до первого ответа, или отказ. */
function LeadsBody({ leads, total, onTurn, onReset, ...filterRow }: Props) {
  const { table, answer, rows, refusal } = leads;
  if (table === undefined) {
    // Первого ответа ещё нет — или не будет.
    return refusal !== null ? (
      <LeadsRefused refusal={refusal} />
    ) : (
      <Loader aria-label="Загружаем лидов" m="md" />
    );
  }
  const { filters, hypotheses } = filterRow;
  const nameOf = (id: number) => hypotheses.find((row) => row.id === id)?.name;
  return (
    <>
      <LeadsTable
        {...filterRow}
        rows={rows}
        stale={leads.stale}
        refusal={refusal}
        empty={
          answer !== undefined && rows.length === 0 ? emptinessOf(filters, total, nameOf) : null
        }
        reasons={Object.keys(table.reasons)}
        onReset={onReset}
      />
      <PageSwitch
        label="Страницы лидов"
        page={filters.page}
        pages={leads.pages}
        onChange={onTurn}
        pt="xs"
      />
    </>
  );
}
