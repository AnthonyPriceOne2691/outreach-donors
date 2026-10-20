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
 *
 * **Очистка — здесь же, по гипотезе** (`CleanLeads`): выбрана гипотеза и у неё есть
 * новые лиды — строка «N лидов ждут очистки» и кнопка между плитками и таблицей. Число —
 * сервера, из того же списка гипотез (`cleanData.waitingOf`): бывает меньше плитки «Новые» —
 * новый лид с первым ответом `unknown` ждёт дня перепроверки. Очистка идёт по одной
 * гипотезе, поэтому без выбранной гипотезы строки нет.
 *
 * **Правка лида — окном из строки** (`LeadEditModal`): сохранили — уведомление, и если письмо
 * лида ждёт в очереди, сказано, что следующая сборка соберёт его заново; лиды и счётчики
 * раздела перечитываются.
 */

import { Box, Loader, SimpleGrid } from '@mantine/core';
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { refusalOf } from '../api/client';
import { listLeads } from '../api/sales';
import { LEAD_STATES } from '../api/salesLabels';
import type { LeadChanged } from '../api/salesLeads';
import type { HypothesisCard, LeadCard, LeadState, LeadsView } from '../api/salesTypes';
import { Metric } from '../components/Metric';
import { PageSwitch } from '../components/PageSwitch';
import { formatNumber } from '../format';
import { notify } from '../notices';
import { backwardsOf, emptinessOf, LEAD_STATE_KEYS, leadsLink, queryOf } from './leadFilters';
import type { LeadFilters } from './leadFilters';
import { addedProblem } from './leadPeriod';
import { waitingOf } from './cleanData';
import { CleanLeads } from './CleanLeads';
import { LeadEditModal } from './LeadEditModal';
import { LeadsEmpty, LeadsRefused, LeadsTable } from './LeadsTable';
import type { FilterRowProps } from './LeadsTable';
import { useMayWrite } from './WriteRight';

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

/** Лиды под фильтрами. На вкладке гипотез таблицы лидов нет — и запроса за ней нет; период
 *  «с» позже «по» пуст по определению — за ним запроса тоже нет, что не так — сказано словами. */
export function useLeads(filters: LeadFilters): Leads {
  const leads = useQuery({
    queryKey: [
      ...LEADS_QUERY_KEY,
      filters.state,
      filters.reason,
      filters.hypothesis,
      filters.search,
      filters.addedFrom,
      filters.addedTo,
      filters.page,
    ],
    queryFn: () => listLeads(queryOf(filters)),
    placeholderData: keepPreviousData,
    enabled: filters.tab === 'leads' && addedProblem(filters.addedFrom, filters.addedTo) === null,
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
  const chosen = hypotheses.find((row) => row.id === filters.hypothesis);
  return (
    <>
      <LeadTiles hypotheses={hypotheses} hypothesis={filters.hypothesis} />
      {/* Своя память задачи у каждой гипотезы: смена гипотезы — новая строка, не прежняя.
          Поле — у самой строки: пустая обёртка добавила бы зазор стопки между плитками и
          таблицей. */}
      {chosen !== undefined ? (
        <CleanLeads
          key={chosen.id}
          hypothesis={chosen.id}
          waiting={waitingOf(chosen)}
          recheck={chosen.recheck ?? 0}
          px="md"
        />
      ) : null}
      <LeadsBody leads={leads} total={total} onTurn={onTurn} onReset={onReset} {...filterRow} />
    </>
  );
}

/** Сохранили правку: что вышло — словами, лиды и счётчики раздела — заново. */
function useLeadSaved(close: () => void) {
  const client = useQueryClient();
  return async (saved: LeadChanged) => {
    close();
    const again = saved.rebuild ? ' Письмо в очереди соберёт заново следующая сборка.' : '';
    const said = saved.changed.length > 0 ? `Лид ${saved.email} сохранён.` : 'Без изменений.';
    notify({ message: `${said}${again}`, color: 'green' });
    await client.invalidateQueries({ queryKey: ['sales'] });
  };
}

/** Что встаёт в таблицу: отказ или объяснение пустоты вместо строк, коды причин для фильтра,
 *  число страниц. `null` — показывать ещё нечего: первого ответа нет. Период «с» позже «по» —
 *  таблица и без ответа: из ссылки с таким периодом запроса нет, а без шапки даты было бы не
 *  поправить. */
function tableOf(
  leads: Leads,
  filters: LeadFilters,
  total: number,
  nameOf: (id: number) => string | undefined,
) {
  const reasons = Object.keys(leads.table?.reasons ?? {});
  const backwards = backwardsOf(filters);
  if (backwards !== null) return { empty: backwards, refusal: null, reasons, pages: 1 };
  if (leads.table === undefined) return null;
  const nothing = leads.answer !== undefined && leads.rows.length === 0;
  return {
    empty: nothing ? emptinessOf(filters, total, nameOf) : null,
    refusal: leads.refusal,
    reasons,
    pages: leads.pages,
  };
}

/** Таблица со страницами — или крутилка до первого ответа, или отказ. */
function LeadsBody({ leads, total, onTurn, onReset, ...filterRow }: Props) {
  const mayWrite = useMayWrite();
  const [editing, setEditing] = useState<LeadCard | null>(null);
  const saved = useLeadSaved(() => setEditing(null));
  const { filters, hypotheses } = filterRow;
  const shown = tableOf(
    leads,
    filters,
    total,
    (id) => hypotheses.find((row) => row.id === id)?.name,
  );
  if (shown === null) {
    // Первого ответа ещё нет — или не будет.
    return leads.refusal !== null ? (
      <LeadsRefused refusal={leads.refusal} />
    ) : (
      <Loader aria-label="Загружаем лидов" m="md" />
    );
  }
  return (
    <>
      <LeadsTable
        {...filterRow}
        rows={leads.rows}
        stale={leads.stale}
        refusal={shown.refusal}
        empty={shown.empty}
        reasons={shown.reasons}
        onReset={onReset}
        mayWrite={mayWrite}
        onEdit={setEditing}
      />
      <PageSwitch
        label="Страницы лидов"
        page={filters.page}
        pages={shown.pages}
        onChange={onTurn}
        pt="xs"
      />
      {editing !== null && (
        <LeadEditModal
          key={editing.id}
          lead={editing}
          mayWrite={mayWrite}
          onClose={() => setEditing(null)}
          onSaved={(changed) => void saved(changed)}
        />
      )}
    </>
  );
}
