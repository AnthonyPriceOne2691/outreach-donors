/**
 * Вкладка лидов: запрос под фильтрами, таблица и страницы.
 *
 * **Смена фильтра — не перезагрузка.** Прежние строки стоят приглушёнными,
 * пока едут новые; отказ сервера на новом фильтре встаёт строкой в таблицу,
 * а фильтры остаются — условие поправляют тут же. Крутилка — только до первого
 * ответа: дальше вкладка не пропадает.
 *
 * Запрос (`useLeads`) зовёт страница раздела, а не эта вкладка: он уходит
 * вместе со списком гипотез, а не после него — пока едут гипотезы, вкладки
 * на экране ещё нет.
 */

import { Loader } from '@mantine/core';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { refusalOf } from '../api/client';
import { listLeads } from '../api/sales';
import type { LeadCard, LeadsView } from '../api/salesTypes';
import { PageSwitch } from '../components/PageSwitch';
import { emptinessOf, queryOf } from './leadFilters';
import type { LeadFilters } from './leadFilters';
import { LeadsRefused, LeadsTable } from './LeadsTable';
import type { FilterRowProps } from './LeadsTable';

const LEADS_QUERY_KEY = ['sales', 'leads'] as const;

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
