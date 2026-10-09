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
 * Вкладка, фильтры и страница лидов, гипотеза раздела и период воронки живут в адресе
 * (`leadFilters.ts`): `?state=rejected&reason=duplicate` переживает перезагрузку, а
 * `?hypothesis=` — ещё и смену вкладки.
 *
 * **Смена фильтра — не перезагрузка.** Прежние строки стоят приглушёнными,
 * пока едут новые; отказ сервера на новом фильтре встаёт под шапкой таблицы,
 * а плитки и фильтры остаются — условие поправляют тут же.
 */

import { Button, Card, Group, SegmentedControl, Select, Stack, Title } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo } from 'react';
import type { ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { listHypotheses } from '../api/sales';
import { InfoHint } from '../components/InfoHint';
import { useTyped } from '../donors/useTyped';
import { formatNumber } from '../format';
import { dropdownBelow } from '../theme';
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
  SETTINGS_TABS,
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

/** Вкладки раздела с числами. Где они не влезают в панель — список «Раздел»: на телефоне
 *  семь вкладок столбиком ставили таблицу на 204 px ниже (аудит экранов 09.10.2026), а на
 *  800–1024 px вкладки в ряд срезались краем панели — «Воронка» и настройки были
 *  недосягаемы. Вкладкам в ряд нужно 791 px, панели их столько — с окна в 1 062 px; список —
 *  до 75em, с запасом на числа побольше. В списке рабочие вкладки, черта и настройки —
 *  порядок тот же, что у вкладок. */
function SalesTabs({ tab, counts, onTab }: TabsProps) {
  const narrow = useMediaQuery('(max-width: 75em)');
  const pick = (value: string | null) => {
    const next = SALES_TAB_KEYS.find((key) => key === value);
    if (next !== undefined && next !== tab) onTab(next);
  };
  const item = (key: SalesTab) => ({ value: key, label: tabLabel(key, counts[key]) });
  if (narrow) {
    const settings = SALES_TAB_KEYS.filter((key) => SETTINGS_TABS.has(key));
    return (
      <Select
        aria-label="Раздел"
        allowDeselect={false}
        value={tab}
        onChange={pick}
        data={[
          ...SALES_TAB_KEYS.filter((key) => !SETTINGS_TABS.has(key)).map(item),
          { group: 'Настройки', items: settings.map(item) },
        ]}
        // Все семь пунктов без прокрутки списка. На телефоне рядом в ряд встаёт «Загрузить
        // базу», и поле уже пункта «База знаний — 3» — список по содержимому, левым краем по
        // полю; на окне пошире поле — по значению, не шире 16rem, а не на весь ряд.
        maxDropdownHeight={360}
        comboboxProps={{ ...dropdownBelow, width: 'max-content' }}
        maw="16rem"
        style={{ flex: '1 1 8rem' }}
      />
    );
  }
  return (
    <SegmentedControl
      aria-label="Вкладки продаж"
      value={tab}
      onChange={pick}
      data={SALES_TAB_KEYS.map(item)}
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
  // Гипотеза, которой нет в списке (старая ссылка), вкладкам с выбором — «не выбрана»:
  // поле выбора её не покажет, а запрос ушёл бы в пустоту. Лиды называют её номером.
  const chosen = known.some((row) => row.id === filters.hypothesis) ? filters.hypothesis : null;
  const pick = (hypothesis: number | null) => apply({ hypothesis });
  // Другая вкладка — чистый адрес, кроме гипотезы: она одна на раздел (аудит экранов
  // 09.10.2026). «Сбросить фильтры» снимает и её: пусто могло быть из-за неё.
  // Набранный поиск уходит вместе с адресом.
  const go = (next: LeadFilters) => {
    setSearch('');
    setParams(writeLeadFilters(next), { replace: true });
  };
  const switchTab = (tab: SalesTab) =>
    go({ ...NO_LEAD_FILTERS, tab, hypothesis: filters.hypothesis });

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
        onReset={() => go(NO_LEAD_FILTERS)}
      />
    ),
    hypotheses: () => <HypothesesTable rows={known} />,
    kb: () => <KbPane />,
    sender: () => <SenderPane />,
    chain: () => <ChainPane hypotheses={known} owner={chosen} onOwner={pick} />,
    queue: () => <QueuePane hypotheses={known} hypothesis={chosen} onHypothesis={pick} />,
    funnel: () => (
      <FunnelPane
        hypotheses={known}
        filters={{ hypothesis: chosen, period: filters.period, from: filters.from, to: filters.to }}
        onChange={apply}
      />
    ),
  };

  return (
    <Card className="glassPanel" p="md">
      <Stack gap="sm">
        <SalesTitle />
        <Group justify="space-between" align="center" gap="sm" wrap="wrap" className="salesTabs">
          <SalesTabs
            tab={filters.tab}
            counts={{ leads: total, hypotheses: known.length, kb: kb.data?.total }}
            onTab={switchTab}
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
