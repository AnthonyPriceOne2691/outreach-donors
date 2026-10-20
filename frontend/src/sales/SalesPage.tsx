/**
 * Продажи: гипотезы, лиды и дорога к загрузке базы.
 *
 * **Экран показывает то, что записали загрузка и очистка, и ничего не решает
 * сам.** Состояние лида и причина отказа — те, что в базе; экран называет их
 * словами и даёт по ним сузить таблицу. Очистку запускает кнопка на вкладке лидов
 * (`CleanLeads`, задачей очереди продаж), гипотезу заводит окно «Новая гипотеза» —
 * консоль для них не нужна; здесь виден их след: сколько готово к письмам, сколько и
 * почему отсеяно.
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

import { Box, Button, Card, Group, SegmentedControl, Select, Stack, Text } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import type { SetURLSearchParams } from 'react-router-dom';

import { listHypotheses } from '../api/sales';
import type { HypothesisCard } from '../api/salesTypes';
import { PageHead } from '../components/PageHead';
import { useTyped } from '../donors/useTyped';
import { formatNumber } from '../format';
import { Watchdog } from '../layout/Watchdog';
import { dropdownBelow } from '../theme';
import { AgentQualityPane } from './AgentQualityPane';
import { ChainPane } from './ChainPane';
import { FunnelPane } from './FunnelPane';
import { HypothesesTable } from './HypothesesTable';
import { HYPOTHESES_QUERY_KEY } from './hypothesisData';
import { NewHypothesisButton } from './HypothesisModal';
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
import { ModuleOff } from './ModuleOff';
import { Pending } from './Pending';
import { QueuePane } from './QueuePane';
import { SegmentPane } from './SegmentPane';
import { SenderPane } from './SenderPane';
import { StoplistPane } from './StoplistPane';
import { WeeklyPane } from './WeeklyPane';

/** Набранный поиск совпадает с адресом без пробелов по краям. */
const sameSearch = (draft: string, committed: string) => draft.trim() === committed;

/** Шапка раздела — общей шапкой экранов (`components/PageHead`): заголовок и «i» — как
 *  устроен раздел, это читают раз, а не на каждом заходе. Поле — кромка текста таблиц. */
function SalesTitle() {
  return (
    <Box px="md">
      <PageHead
        title="Продажи"
        hint="Лиды попадают сюда из файла или Google-таблицы, проходят очистку — дубли, стоп-листы, почта домена, проверка адреса — и готовыми уходят в письма. У каждого отсеянного названа причина: кодом, по которому фильтр, и словами, что именно нашлось."
      />
    </Box>
  );
}

/** Вкладки, где загрузка базы — дело вкладки: лиды из неё и берутся, гипотезе её грузят.
 *  На остальных кнопка спорила с главной кнопкой вкладки — до трёх залитых кнопок разом
 *  (аудит экранов 09.10.2026). Стоит сразу за вкладками, слева: в правом краю строки
 *  между ними лежала пустая полоса во всю ширину (обход экранов 10.10.2026).
 *
 *  На вкладке гипотез за ней — «Новая гипотеза», не залитая: залитая на вкладке одна, и это
 *  «Загрузить базу» — главное действие раздела, на «Лидах» и «Гипотезах» на одном месте и
 *  одного веса (кнопка, меняющая вес между соседними вкладками, читалась бы другим
 *  действием). Гипотезу заводят реже, чем грузят базу, и мастер загрузки заводит её сам —
 *  она больше не ворота к загрузке. Рядом, а не над таблицей: действия вкладки — одной
 *  строкой сразу за переключателем, как велит обход 10.10. */
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
 *  недосягаемы. Порог — по снимкам стенда 12.10.2026 (ревью стыков), перемерен 20.10.2026 после
 *  переименования «Лиды» в «emails» (на 5 px шире): одиннадцати вкладкам в ряд нужно 1 206 px
 *  при «emails — 12 345», «Гипотезы — 3», «База знаний — 14» и 1 230 px при «123 456», «12» и
 *  «128»; строка панели уже окна на 362 px при развёрнутом меню (на 166 px — при свёрнутом).
 *  Ряд влезает с окна 1 567 px, с числами побольше — с 1 591; уже — «Стоп-лист» срезан краем
 *  панели (на 1 536 px — до «Стоп»). Список — до 100em (1 600 px): на 1 601 px у ряда с числами
 *  побольше 10 px запаса. Когда «Продажи» разойдутся на пункты меню, список здесь не
 *  понадобится. В списке рабочие вкладки, черта и настройки — порядок тот же, что у вкладок. */
function SalesTabs({ tab, counts, onTab }: TabsProps) {
  const narrow = useMediaQuery('(max-width: 100em)');
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
        // Все одиннадцать пунктов без прокрутки списка: на 390 px они с чертой «Настройки»
        // занимают 401 px (снимок стенда 12.10.2026), поэтому предел списка — 440 px, а не 360.
        // На телефоне рядом в ряд встаёт «Загрузить базу», и поле уже пункта «База знаний — 3» —
        // список по содержимому, левым краем по полю; на окне пошире поле — по значению, не шире
        // 16rem, а не на весь ряд.
        maxDropdownHeight={440}
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

/** Пропавший номер из адреса и гипотеза, взятая вместо него (первая; гипотез нет — `null`). */
interface LostOne {
  id: number;
  first: number | null;
}

/** Номер из адреса, которого нет в списке гипотез; список не пришёл или номера нет — `null`. */
function goneOf(rows: HypothesisCard[] | undefined, hypothesis: number | null): number | null {
  if (rows === undefined || hypothesis === null) return null;
  return rows.some((row) => row.id === hypothesis) ? null : hypothesis;
}

/**
 * Гипотезы из адреса больше нет — удалили, а номер остался в ссылке коллеге, во вкладке «назад»,
 * в памяти места раздела: раздел берёт первую гипотезу и говорит это одной строкой (`Lost`), а не
 * падает и не показывает пустоту. Прогон из адреса — той гипотезы, он тоже уходит.
 *
 * Удалил её сам человек — первая берётся молча: строка — для чужих ссылок. Удалённая помечается
 * номером (`onRemoved`), а не правкой адреса из окна: перечитанный список приходит раньше смены
 * адреса (навигация роутера идёт переходом, а ответ списка — срочным обновлением), и строка
 * встала бы всё равно (проверено порчей: строка вставала и тому, кто удалил гипотезу сам).
 */
function useLostHypothesis(
  rows: HypothesisCard[] | undefined,
  hypothesis: number | null,
  setParams: SetURLSearchParams,
) {
  const removed = useRef<number | null>(null);
  const [lost, setLost] = useState<LostOne | null>(null);
  const gone = goneOf(rows, hypothesis);
  const first = rows?.[0]?.id ?? null;
  useEffect(() => {
    if (gone === null) return;
    if (gone !== removed.current) setLost({ id: gone, first });
    setParams(
      (current) =>
        writeLeadFilters({ ...readLeadFilters(current), hypothesis: first, run: null, page: 1 }),
      { replace: true },
    );
  }, [gone, first, setParams]);
  return {
    /** Строка «больше нет» — пока выбрана взятая вместо пропавшей гипотеза. */
    shown: lost !== null && hypothesis === lost.first ? lost : null,
    onRemoved: (id: number) => {
      removed.current = id;
    },
  };
}

/** Гипотезы из адреса больше нет: какой номер пропал и какая гипотеза взята вместо него. */
function Lost({ lost, rows }: { lost: LostOne; rows: HypothesisCard[] }) {
  const instead = rows.find((row) => row.id === lost.first);
  return (
    <Text px="md" size="sm" role="status">
      Гипотезы №{lost.id} больше нет —{' '}
      {instead === undefined ? 'гипотез пока нет.' : `выбрана первая: «${instead.name}».`}
    </Text>
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

  const lost = useLostHypothesis(hypotheses.data?.rows, filters.hypothesis, setParams);

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
    hypotheses: () => <HypothesesTable rows={known} onRemoved={lost.onRemoved} />,
    collect: () => (
      <SegmentPane
        hypotheses={known}
        hypothesis={chosen}
        // Прогон — гипотезы: другая гипотеза открывает свой последний прогон.
        onHypothesis={(hypothesis) => apply({ hypothesis, run: null })}
        run={filters.run}
        onRun={(run) => apply({ run })}
      />
    ),
    kb: () => <KbPane />,
    sender: () => <SenderPane />,
    stoplist: () => <StoplistPane />,
    chain: () => <ChainPane hypotheses={known} owner={chosen} onOwner={pick} />,
    queue: () => <QueuePane hypotheses={known} hypothesis={chosen} onHypothesis={pick} />,
    funnel: () => (
      <FunnelPane
        hypotheses={known}
        filters={{ hypothesis: chosen, period: filters.period, from: filters.from, to: filters.to }}
        onChange={apply}
      />
    ),
    agent: () => (
      <AgentQualityPane cohort={filters.cohort} onCohort={(cohort) => apply({ cohort })} />
    ),
    report: () => <WeeklyPane week={filters.week} onWeek={(week) => apply({ week })} />,
  };

  return (
    <Card className="glassPanel" p="md">
      <Stack gap="sm">
        <SalesTitle />
        <ModuleOff view={hypotheses.data} />
        <Watchdog stage="sales" />
        <Group align="center" gap="sm" wrap="wrap" className="salesTabs">
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
          {filters.tab === 'hypotheses' && <NewHypothesisButton />}
        </Group>
        {lost.shown !== null && <Lost lost={lost.shown} rows={known} />}
        {bodies[filters.tab]()}
      </Stack>
    </Card>
  );
}
