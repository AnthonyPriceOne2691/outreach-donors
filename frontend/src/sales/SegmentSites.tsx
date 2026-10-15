/**
 * Все сайты прогона гипотезы — по одному, и пересуд прогона после правки описания сегмента.
 *
 * Тест прогона: человек видел только «на проверку» (до сотни строк) и сводку отказов по
 * причинам, а ни принятых, ни отсечённых по сайтам — не видел; калибровать судью правкой описания
 * было нечем. Судья сортирует, решает человек: здесь полный список — полоса, что сказал судья, кто
 * решил и когда, почему и цитата сайта, итог (лид со ссылкой, только форма, адреса нет, не
 * искали) — и пересуд с «было → стало» (`SegmentSitesRejudge`).
 *
 * **Вкладки — полосы с числами**, страницы — сервера, всего — числом под таблицей: обрезки без
 * пометки нет. Решение человека по «на проверку» — там же, во вкладке «На проверку».
 *
 * **Список свёрнут, пока его не открыли**: числа полос видны и так (отчёт прогона), а таблица на
 * сотни строк — по нажатию. Вкладка, фильтр и страница — в адресе (`?sites_run=N&sites=…`),
 * привязанные к прогону: ссылка коллеге открывает тот же вид, а другой прогон — с начала.
 */

import { Alert, Button, Group, SegmentedControl, Stack, Text, Title } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { refusalOf } from '../api/client';
import { InfoHint } from '../components/InfoHint';
import { PageSwitch } from '../components/PageSwitch';
import { formatNumber } from '../format';
import { decideSite } from './segmentApi';
import type { SegmentLane, SegmentRunDetail, SegmentRunRow } from './segmentApi';
import { SEGMENT_QUERY_KEY } from './segmentData';
import { isActive } from './segmentStage';
import { readSites } from './SegmentSitesApi';
import type {
  SegmentSite,
  SegmentSitesPage,
  SiteDecider,
  SitesQuery,
  SiteTab,
} from './SegmentSitesApi';
import { RejudgeButton, RejudgeStatus, SITES_QUERY_KEY, useRejudge } from './SegmentSitesRejudge';
import { SegmentSitesTable } from './SegmentSitesTable';
import { DECIDER_KEYS, LANE_WORDS, TAB_KEYS, TAB_TITLES } from './SegmentSitesWords';

/** Имена в адресе: чей прогон, вкладка, кто решил, страница. */
const PARAM = { run: 'sites_run', tab: 'sites', by: 'sites_by', page: 'sites_page' } as const;

/** Пока прогон идёт, список опрашивается сам: судья и адреса меняют полосы на глазах. */
const SITES_POLL_MS = 5_000;

/** Опрос списка сайтов: идёт ли прогон — правилом экрана хода (`segmentStage.isActive`: ответ
 *  очереди задач, у сервера старше — стадия). Продолженный готовый прогон опрашивается, хотя
 *  стадия у него «готово»; прогон, чья задача умерла, — нет, хотя стадия у него «судья». */
export function sitesPoll(run: SegmentRunRow): number | false {
  return isActive(run) ? SITES_POLL_MS : false;
}

/** Узкое окно — вкладки столбиком во всю ширину, как переключатель этапа. */
const NARROW = '(max-width: 36em)';

interface View extends SitesQuery {
  open: boolean;
}

const CLOSED: View = { open: false, tab: 'all', decider: null, page: 1 };

function pageOf(raw: string | null): number {
  const value = raw !== null && /^\d{1,6}$/.test(raw) ? Number(raw) : 1;
  return value >= 1 ? value : 1;
}

/** Вид списка из адреса — только если он про этот прогон. */
function viewOf(params: URLSearchParams, run: number): View {
  if (params.get(PARAM.run) !== String(run)) return CLOSED;
  return {
    open: true,
    tab: TAB_KEYS.find((key) => key === params.get(PARAM.tab)) ?? 'all',
    decider: DECIDER_KEYS.find((key) => key === params.get(PARAM.by)) ?? null,
    page: pageOf(params.get(PARAM.page)),
  };
}

/** Вид → адрес: умолчания не пишутся, закрытый список — без своих имён вовсе. */
function written(current: URLSearchParams, run: number, view: View): URLSearchParams {
  const next = new URLSearchParams(current);
  for (const name of Object.values(PARAM)) next.delete(name);
  if (!view.open) return next;
  next.set(PARAM.run, String(run));
  if (view.tab !== 'all') next.set(PARAM.tab, view.tab);
  if (view.decider !== null) next.set(PARAM.by, view.decider);
  if (view.page > 1) next.set(PARAM.page, String(view.page));
  return next;
}

type Go = (patch: Partial<View>, replace?: boolean) => void;

function useSitesView(run: number): [View, Go] {
  const [params, setParams] = useSearchParams();
  const view = useMemo(() => viewOf(params, run), [params, run]);
  const go = useCallback<Go>(
    (patch, replace) =>
      // Страница — новая запись истории («назад» ведёт по страницам), остальное — замена.
      setParams((current) => written(current, run, { ...viewOf(current, run), ...patch }), {
        replace: replace ?? !('page' in patch),
      }),
    [run, setParams],
  );
  return [view, go];
}

/** Числа вкладок — сервера, с поправкой на решения человека в этом виде: строка решена, а
 *  список ещё не перечитан. */
function countsOf(
  lanes: Record<SiteTab, number>,
  decided: Record<number, SegmentLane>,
): Record<SiteTab, number> {
  const counts = { ...lanes };
  for (const lane of Object.values(decided)) {
    counts.review -= 1;
    if (lane === 'accept' || lane === 'reject') counts[lane] += 1;
  }
  return counts;
}

function Tabs({
  counts,
  tab,
  onTab,
}: {
  counts: Record<SiteTab, number> | null;
  tab: SiteTab;
  onTab: (tab: SiteTab) => void;
}) {
  const narrow = useMediaQuery(NARROW, false, { getInitialValueInEffect: false }) === true;
  // «Сменили при пересуде» — только когда пересуд что-то сменил: вкладка, где пусто по
  // определению, ничего не говорит.
  const shown = TAB_KEYS.filter((key) => key !== 'changed' || (counts?.changed ?? 0) > 0);
  return (
    <SegmentedControl
      aria-label="Полоса сайтов"
      value={tab}
      onChange={(picked) => onTab(TAB_KEYS.find((key) => key === picked) ?? 'all')}
      data={shown.map((key) => ({
        value: key,
        label:
          counts === null ? TAB_TITLES[key] : `${TAB_TITLES[key]} · ${formatNumber(counts[key])}`,
      }))}
      orientation={narrow ? 'vertical' : 'horizontal'}
      fullWidth={narrow}
      style={{ alignSelf: narrow ? 'stretch' : 'flex-start' }}
    />
  );
}

/** Почему во вкладке пусто — словами. */
const EMPTY: Record<SiteTab, string> = {
  all: 'Выдача не дала прогону ни одного нового сайта.',
  accept: 'Принятых сайтов нет.',
  review: 'Решать человеку нечего: сайтов на проверке нет.',
  reject: 'Отсечённых сайтов нет.',
  pending: 'Судья прошёл все сайты прогона.',
  changed: 'Пересуд не сменил ни одной полосы.',
};

/** Решения человека этого вида: строка стоит на месте с пометкой, пока вид не сменили. */
function useDecisions(shape: string) {
  const client = useQueryClient();
  const [decided, setDecided] = useState<Record<number, SegmentLane>>({});
  useEffect(() => setDecided({}), [shape]);
  const decide = useMutation({
    mutationFn: ({ site, accept }: { site: SegmentSite; accept: boolean }) =>
      decideSite(site.id, accept),
    onSuccess: (row) => {
      setDecided((was) => ({ ...was, [row.id]: row.lane }));
      void client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY });
    },
  });
  return { decided, decide };
}

/** Сколько показано из скольких — и переключатель страниц сервера. */
function Footer({ page, view, go }: { page: SegmentSitesPage; view: View; go: Go }) {
  const first = (page.page - 1) * page.limit + 1;
  const shown =
    page.rows.length > 0
      ? `Показаны ${formatNumber(first)}–${formatNumber(first + page.rows.length - 1)} из ${formatNumber(page.total)}.`
      : `Всего ${formatNumber(page.total)}.`;
  return (
    <>
      <Text size="sm" c="dimmed">
        {shown}
      </Text>
      <PageSwitch
        label="Страницы сайтов прогона"
        page={view.page}
        pages={Math.max(1, Math.ceil(page.total / page.limit))}
        onChange={(next) => go({ page: next })}
      />
    </>
  );
}

/** Страница из старой ссылки за концом — последняя настоящая, а не пустота (как у лидов). */
function usePageBack(page: SegmentSitesPage | undefined, stale: boolean, view: View, go: Go) {
  const last = page === undefined ? 1 : Math.max(1, Math.ceil(page.total / page.limit));
  const beyond = page !== undefined && !stale && view.page > last;
  useEffect(() => {
    if (beyond) go({ page: last }, true);
  }, [beyond, go, last]);
}

function Sites({ detail, view, go }: { detail: SegmentRunDetail; view: View; go: Go }) {
  const run = detail.run.id;
  const query: SitesQuery = { tab: view.tab, decider: view.decider, page: view.page };
  const sites = useQuery({
    queryKey: [...SITES_QUERY_KEY, run, query.tab, query.decider, query.page],
    queryFn: () => readSites(run, query),
    placeholderData: keepPreviousData,
    refetchInterval: sitesPoll(detail.run),
  });
  const { decided, decide } = useDecisions(
    `${run}/${query.tab}/${query.decider ?? ''}/${query.page}`,
  );
  const page = sites.data;
  usePageBack(page, sites.isPlaceholderData, view, go);
  if (page === undefined) {
    return sites.error !== null ? (
      <Alert color="red" title="Сайты прогона не загрузились">
        {refusalOf(sites.error)}
      </Alert>
    ) : (
      <Text size="sm" c="dimmed">
        Загружаем сайты прогона…
      </Text>
    );
  }
  const empty = page.total === 0 && page.rows.length === 0;
  return (
    <Stack gap="sm">
      <Tabs
        counts={countsOf(page.lanes, decided)}
        tab={view.tab}
        onTab={(tab) => go({ tab, decider: null, page: 1 })}
      />
      {decide.error !== null && (
        <Alert color="red" title="Решение не записано">
          {refusalOf(decide.error)}
        </Alert>
      )}
      {empty ? (
        <Text size="sm" c="dimmed">
          {view.decider !== null ? 'Под этим фильтром сайтов нет.' : EMPTY[view.tab]}
        </Text>
      ) : (
        <SegmentSitesTable
          tab={view.tab}
          page={page}
          decider={view.decider}
          onDecider={(decider: SiteDecider | null) => go({ decider, page: 1 })}
          decided={decided}
          busy={decide.isPending}
          stale={sites.isPlaceholderData}
          onDecide={(site, accept) => decide.mutate({ site, accept })}
        />
      )}
      <Footer page={page} view={view} go={go} />
    </Stack>
  );
}

/** Числа полос одной строкой — из отчёта прогона: видны и при свёрнутом списке. */
function lanesLine(detail: SegmentRunDetail): string {
  const lanes = detail.lanes;
  const parts = (['accept', 'review', 'reject', 'pending'] as const)
    .filter((lane) => lane !== 'pending' || (lanes.pending ?? 0) > 0)
    .map((lane) => `${LANE_WORDS[lane]} — ${formatNumber(lanes[lane] ?? 0)}`);
  return parts.join(', ');
}

export function SegmentSites({ detail }: { detail: SegmentRunDetail }) {
  const run = detail.run.id;
  const [view, go] = useSitesView(run);
  const [started, setStarted] = useState(false);
  // Ход пересуда спрашивается, только если пересуд был или его только что поставили.
  const rejudge = useRejudge(run, started || detail.report.rejudge !== undefined);
  const total = Object.values(detail.lanes).reduce((sum, count) => sum + count, 0);
  return (
    <Stack gap="sm">
      <Group justify="space-between" gap="xs" wrap="wrap">
        <Group gap={4} wrap="nowrap">
          <Title order={5}>Сайты прогона</Title>
          <InfoHint name="Что в списке сайтов" width={360}>
            Каждый сайт прогона: полоса, что сказал судья, кто решил и когда, почему и цитата сайта,
            итог поиска адреса. Пересуд — после правки описания сегмента: те же сайты и тот же
            текст, новый вопрос судье; что сменилось — числами и вкладкой «Сменили при пересуде».
          </InfoHint>
        </Group>
        <RejudgeButton
          run={run}
          resumes={rejudge.data?.estimate.resumes ?? false}
          onStarted={() => setStarted(true)}
        />
      </Group>
      <RejudgeStatus
        view={rejudge.data}
        onChanged={() => go({ open: true, tab: 'changed', decider: null, page: 1 })}
      />
      <Group gap="sm" wrap="wrap">
        <Button
          variant="default"
          size="compact-sm"
          className="press"
          aria-expanded={view.open}
          onClick={() => go(view.open ? { open: false } : { open: true })}
        >
          {view.open ? 'Скрыть сайты' : `Показать сайты · ${formatNumber(total)}`}
        </Button>
        {!view.open && total > 0 && (
          <Text size="sm" c="dimmed">
            {lanesLine(detail)}
          </Text>
        )}
      </Group>
      {view.open && <Sites detail={detail} view={view} go={go} />}
    </Stack>
  );
}
