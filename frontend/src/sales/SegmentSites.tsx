/**
 * Все сайты прогона гипотезы — по одному, в окне «Сайты прогона»; там же пересуд прогона после
 * правки описания сегмента и решение человека по «на проверку».
 *
 * Тест прогона: человек видел только «на проверку» (до сотни строк) и сводку отказов по
 * причинам, а ни принятых, ни отсечённых по сайтам — не видел; калибровать судью правкой описания
 * было нечем. Судья сортирует, решает человек: здесь полный список — полоса, что сказал судья, кто
 * решил и когда, почему и цитата сайта, итог (лид со ссылкой, только форма, адреса нет, не
 * искали) — и пересуд с «было → стало» (`SegmentSitesRejudge`).
 *
 * **Окно, а не простыня в отчёте** (15.10.2026): отчёт читается за один-два экрана — ход, плитки,
 * что ждёт человека; список открывают плитки полос, «Сайты прогона · N» у заголовка и «На проверку
 * N — решить», каждая — на своей вкладке. Вид окна — в адресе (`SegmentSitesView.ts`): окно — одна
 * запись истории, вкладка и страница в нём — замена записи, «назад» закрывает окно.
 *
 * **Вкладки — полосы с числами**, страницы — сервера, всего — числом под таблицей: обрезки без
 * пометки нет. **Решение по «на проверку» — на её вкладке**: решённая строка стоит на месте с
 * пометкой («принят — записываем…», потом «решено: принят»), пока вид не сменили, — и тогда, когда
 * опрос идущего прогона уже принёс список без неё: нижние строки не поднимаются под курсор.
 * Повтор того же решения сервер принимает (200), противоположное — отказ словами (409) над таблицей.
 *
 * **«Отменить решение»** (тест продаж 20.10) — у решения человека, пока сервер говорит, что можно
 * (`undoable`): сайт возвращается туда и со словами, куда его кладёт судья, — на «На проверку» снова
 * «Принять» и «Отказ». Строка, по которой отменили, стоит на месте на любой вкладке, числа вкладок и
 * отчёта следуют за ответом сервера без перезагрузки; лид завели посреди — отказ словами сервера.
 */

import { Alert, Group, SegmentedControl, Stack, Text } from '@mantine/core';
import { useMediaQuery } from '@mantine/hooks';
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useState } from 'react';

import { refusalOf } from '../api/client';
import { InfoHint } from '../components/InfoHint';
import { PageSwitch } from '../components/PageSwitch';
import { formatNumber } from '../format';
import type { SegmentRunDetail, SegmentRunRow } from './segmentApi';
import { SEGMENT_QUERY_KEY } from './segmentData';
import { ReportWindow } from './SegmentReportWindow';
import { isActive } from './segmentStage';
import { decideSite, readSites, undoDecision } from './SegmentSitesApi';
import type {
  RejudgeView,
  SegmentSite,
  SegmentSitesPage,
  SiteDecider,
  SitesQuery,
  SiteTab,
} from './SegmentSitesApi';
import type { Held, SiteAction } from './SegmentSitesDecide';
import { RejudgeButton, RejudgeStatus, SITES_QUERY_KEY } from './SegmentSitesRejudge';
import { SegmentSitesTable } from './SegmentSitesTable';
import type { SitesGo, SitesView, SitesWindow } from './SegmentSitesView';
import { TAB_KEYS, TAB_TITLES } from './SegmentSitesWords';

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

/** Ширина окна на широком экране: таблице «Все» нужно 1 032 px, «На проверку» — 1 040, а причине
 *  в остатке — места на две-три строки, а не на пять. Уже экрана окно не бывает (`max-width`). */
const WINDOW_SIZE = '80rem';

/**
 * Числа вкладок — сервера, с поправкой на решения и отмены человека, которых сервер в этом ответе
 * ещё не видел: строка в нём — в прежней полосе. Список, перечитанный после действия (опрос идущего
 * прогона), уже считает его сам — строка в нём в новой полосе или ушла с вкладки: второй раз не
 * переносится.
 */
export function countsOf(
  page: SegmentSitesPage,
  held: Record<number, Held>,
): Record<SiteTab, number> {
  const counts = { ...page.lanes };
  const served = new Map(page.rows.map((site) => [site.id, site.lane]));
  for (const [id, { site, doing }] of Object.entries(held)) {
    const was = served.get(Number(id));
    if (doing !== null || was === undefined || was === site.lane) continue;
    counts[was] -= 1;
    counts[site.lane] += 1;
  }
  return counts;
}

/**
 * Строки вкладки: ответ сервера, у строк, по которым действовали здесь, — то, что записал сервер,
 * и все — на своих местах, по порядку сервера (номер сайта). Опрос идущего прогона приносит вкладку
 * уже без такой строки («на проверку» — без решённой, «приняты» — без отменённой) — она остаётся,
 * пока вид не сменили, и строки под ней не поднимаются под курсор.
 */
export function rowsOf(page: SegmentSitesPage, held: Record<number, Held>): SegmentSite[] {
  const shown = new Map(page.rows.map((site) => [site.id, held[site.id]?.site ?? site]));
  for (const { site } of Object.values(held)) if (!shown.has(site.id)) shown.set(site.id, site);
  return [...shown.values()].sort((a, b) => a.id - b.id);
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

/**
 * Решения человека этого вида и их отмены: строка стоит на месте с тем, что записал сервер, пока вид
 * не сменили. Пометка встаёт сразу по нажатию («записываем…», «отменяем решение…»): второе нажатие
 * по строке не уходит, а соседние строки решаются, не дожидаясь ответа. Записано — строка сервера на
 * месте прежней, отчёт прогона перечитывается (плитки и «На проверку N»); отказ сервера возвращает
 * строку, какой она была, — с её кнопками.
 */
function useDecisions(shape: string) {
  const client = useQueryClient();
  const [held, setHeld] = useState<Record<number, Held>>({});
  useEffect(() => setHeld({}), [shape]);
  const act = useMutation({
    mutationFn: ({ site, action }: { site: SegmentSite; action: SiteAction }) =>
      action === 'undo' ? undoDecision(site.id) : decideSite(site.id, action === 'accept'),
    onMutate: ({ site, action }) =>
      setHeld((was) => ({ ...was, [site.id]: { site, doing: action } })),
    onSuccess: (row, { site }) => {
      // Ответ — строкой списка; у сервера старше её — только поля решения: остальное — прежнее.
      setHeld((was) => ({ ...was, [site.id]: { site: { ...site, ...row }, doing: null } }));
      void client.invalidateQueries({ queryKey: SEGMENT_QUERY_KEY });
    },
    onError: (_failure, { site }) =>
      setHeld((was) => ({ ...was, [site.id]: { site, doing: null } })),
  });
  return { held, act };
}

/** Отказ сервера на последнее действие — заголовком по действию, словами сервера. */
function ActRefusal({ error, action }: { error: Error | null; action: SiteAction | undefined }) {
  if (error === null) return null;
  return (
    <Alert color="red" title={action === 'undo' ? 'Решение не отменено' : 'Решение не записано'}>
      {refusalOf(error)}
    </Alert>
  );
}

/** Сколько показано из скольких — и переключатель страниц сервера. */
function Footer({ page, view, go }: { page: SegmentSitesPage; view: SitesView; go: SitesGo }) {
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
function usePageBack(
  page: SegmentSitesPage | undefined,
  stale: boolean,
  view: SitesView,
  go: SitesGo,
) {
  const last = page === undefined ? 1 : Math.max(1, Math.ceil(page.total / page.limit));
  const beyond = page !== undefined && !stale && view.page > last;
  useEffect(() => {
    if (beyond) go({ page: last });
  }, [beyond, go, last]);
}

function Sites({ detail, view, go }: { detail: SegmentRunDetail; view: SitesView; go: SitesGo }) {
  const run = detail.run.id;
  const query: SitesQuery = { tab: view.tab, decider: view.decider, page: view.page };
  const sites = useQuery({
    queryKey: [...SITES_QUERY_KEY, run, query.tab, query.decider, query.page],
    queryFn: () => readSites(run, query),
    placeholderData: keepPreviousData,
    refetchInterval: sitesPoll(detail.run),
  });
  const { held, act } = useDecisions(`${run}/${query.tab}/${query.decider ?? ''}/${query.page}`);
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
  const rows = rowsOf(page, sites.isPlaceholderData ? {} : held);
  const empty = page.total === 0 && rows.length === 0;
  return (
    <Stack gap="sm">
      <Tabs
        counts={countsOf(page, held)}
        tab={view.tab}
        onTab={(tab) => go({ tab, decider: null, page: 1 })}
      />
      <ActRefusal error={act.error} action={act.variables?.action} />
      {empty ? (
        <Text size="sm" c="dimmed">
          {view.decider !== null ? 'Под этим фильтром сайтов нет.' : EMPTY[view.tab]}
        </Text>
      ) : (
        <SegmentSitesTable
          tab={view.tab}
          page={{ ...page, rows }}
          decider={view.decider}
          onDecider={(decider: SiteDecider | null) => go({ decider, page: 1 })}
          held={held}
          stale={sites.isPlaceholderData}
          onAct={(site, action) => act.mutate({ site, action })}
        />
      )}
      <Footer page={page} view={view} go={go} />
    </Stack>
  );
}

interface WindowProps {
  detail: SegmentRunDetail;
  /** Вид окна в адресе — его же читают ссылки отчёта, которые окно открывают. */
  sites: SitesWindow;
  /** Пересуд прогона — за ним следит отчёт (`useRejudgeWatch`). */
  rejudge: { view: RejudgeView | undefined; onStarted: () => void };
}

/** Окно «Сайты прогона»: пересуд, его ход и итог, вкладки-полосы, таблица и страницы. */
export function SegmentSitesWindow({ detail, sites, rejudge }: WindowProps) {
  const run = detail.run.id;
  const { view, go, close } = sites;
  // Окно закрывается с переходом: до его конца в нём — прежний вид, а не «Все» закрытого адреса
  // (иначе на глазах ушёл бы запрос другой вкладки).
  const [shut, setShut] = useState(view);
  useEffect(() => {
    if (view.open) setShut(view);
  }, [view]);
  // Поверх — окно сметы пересуда: Esc закрывает его, а не оба разом.
  const [over, setOver] = useState(false);
  const onWindow = useCallback((opened: boolean) => setOver(opened), []);
  return (
    <ReportWindow
      opened={view.open}
      onClose={close}
      title={`Сайты прогона №${run}`}
      size={WINDOW_SIZE}
      closeOnEscape={!over}
    >
      <Stack gap="sm">
        <Group gap="xs" wrap="wrap">
          <RejudgeButton
            run={run}
            resumes={rejudge.view?.estimate.resumes ?? false}
            onStarted={rejudge.onStarted}
            onWindow={onWindow}
          />
          <InfoHint name="Что в списке сайтов" width={360}>
            Каждый сайт прогона: полоса, что сказал судья, кто решил и когда, почему и цитата сайта,
            итог поиска адреса. «Отказ: …» у сайта на проверке — мнение судьи в наблюдении: решает
            человек. Решение человека отменяется кнопкой «Отменить решение», пока по сайту не
            заведён лид: сайт вернётся туда, куда его положил судья. Пересуд — после правки описания
            сегмента: те же сайты и тот же текст, новый вопрос судье; что сменилось — числами и
            вкладкой «Сменили при пересуде».
          </InfoHint>
        </Group>
        <RejudgeStatus
          view={rejudge.view}
          onChanged={() => go({ tab: 'changed', decider: null, page: 1 })}
        />
        <Sites detail={detail} view={view.open ? view : shut} go={go} />
      </Stack>
    </ReportWindow>
  );
}
