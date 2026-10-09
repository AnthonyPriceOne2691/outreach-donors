/**
 * Продажи: гипотезы, лиды и дорога к загрузке базы.
 *
 * **Экран показывает то, что записали загрузка и очистка, и ничего не решает
 * сам.** Состояние лида и причина отказа — те, что в базе; экран называет их
 * словами и даёт по ним сузить таблицу. Очистка идёт командой, здесь виден её
 * след: сколько готово к письмам, сколько и почему отсеяно.
 *
 * **Шапка — одной строкой**: заголовок и «i» с пояснением раздела (аудит экранов
 * 09.10.2026: пять плиток и абзац висели над всеми вкладками). Плитки сводки — на
 * вкладке лидов (`LeadsPane`), числа лидов и гипотез — на вкладках. Считается всё
 * по гипотезам: один запрос даёт и вкладку гипотез, и числа, и список для фильтра.
 * Фильтры, вкладка и страница живут в адресе (`leadFilters.ts`):
 * `?state=rejected&reason=duplicate` переживает перезагрузку.
 *
 * **Смена фильтра — не перезагрузка.** Прежние строки стоят приглушёнными,
 * пока едут новые; отказ сервера на новом фильтре встаёт под шапкой таблицы,
 * а плитки и фильтры остаются — условие поправляют тут же.
 */

import { Button, Card, Group, SegmentedControl, Stack, Title } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo } from 'react';
import type { ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { listHypotheses } from '../api/sales';
import { InfoHint } from '../components/InfoHint';
import { useTyped } from '../donors/useTyped';
import { formatNumber } from '../format';
import { ChainPane } from './ChainPane';
import { FunnelPane } from './FunnelPane';
import { HypothesesTable } from './HypothesesTable';
import { useKb } from './kbData';
import { KbPane } from './KbPane';
import {
  NO_LEAD_FILTERS,
  readLeadFilters,
  SALES_TAB_KEYS,
  SALES_TABS,
  writeLeadFilters,
} from './leadFilters';
import type { LeadFilters, SalesTab } from './leadFilters';
import { LeadsPane, pageBack, useLeads } from './LeadsPane';
import { Pending } from './Pending';
import { QueuePane } from './QueuePane';
import { SenderPane } from './SenderPane';

export const HYPOTHESES_QUERY_KEY = ['sales', 'hypotheses'] as const;

/** Набранный поиск совпадает с адресом без пробелов по краям. */
const sameSearch = (draft: string, committed: string) => draft.trim() === committed;

/** Заголовок раздела и «i»: что за раздел, читают раз, а не на каждом заходе. */
function SalesTitle() {
  return (
    <Group gap="xs" wrap="nowrap" px="md">
      <Title order={3}>Продажи</Title>
      <InfoHint name="Откуда лиды и как их чистят" width={340}>
        Лиды попадают сюда из файла или Google-таблицы, проходят очистку — дубли, стоп-листы, почта
        домена, проверка адреса — и готовыми уходят в письма. У каждого отсеянного названа причина:
        кодом, по которому фильтр, и словами, что именно нашлось.
      </InfoHint>
    </Group>
  );
}

/** Вкладки, где загрузка базы — дело вкладки: лиды из неё и берутся, гипотезе её грузят.
 *  На остальных кнопка спорила с главной кнопкой вкладки — до трёх залитых кнопок разом
 *  (аудит экранов 09.10.2026). */
const UPLOAD_TABS: ReadonlySet<SalesTab> = new Set(['leads', 'hypotheses']);

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
  const total = known.reduce((sum, row) => sum + row.total, 0);
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
        total={total}
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
    chain: () => <ChainPane hypotheses={known} />,
    queue: () => <QueuePane hypotheses={known} />,
    funnel: () => <FunnelPane hypotheses={known} />,
  };

  return (
    <Card className="glassPanel" p="md">
      <Stack gap="sm">
        <SalesTitle />
        <Group justify="space-between" align="center" gap="sm" wrap="wrap" className="salesTabs">
          <SalesTabs
            tab={filters.tab}
            counts={{ leads: total, hypotheses: known.length, kb: kb.data?.total }}
            onTab={reset}
          />
          {UPLOAD_TABS.has(filters.tab) && (
            <Button component={Link} to="/sales/import" className="press">
              Загрузить базу
            </Button>
          )}
        </Group>
        {bodies[filters.tab]()}
      </Stack>
    </Card>
  );
}
