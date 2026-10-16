/**
 * Окно «Сайты прогона» — в адресе: открыто ли, вкладка, кто решил, страница
 * (`?sites_run=N&sites=review…`), привязанные к прогону. Ссылка коллеге и «назад» открывают то же
 * окно на той же вкладке, а другой прогон — без окна. Прогон отчёта ставится в адрес рядом
 * (`run=N`): ссылка открывает его, даже если у гипотезы с тех пор появился прогон новее.
 *
 * **Окно — одна запись истории.** Открывают его ссылки — плитки полос, «Сайты прогона · N» у
 * заголовка, «На проверку N — решить»: новая запись. Вкладка, фильтр и страница внутри окна —
 * замена записи: «назад» закрывает окно, а не листает его вкладки и страницы — как жест «назад»
 * на телефоне, где окно во весь экран.
 *
 * **«Закрыть» и Esc — шаг назад по истории**, если окно открыто ссылкой с отчёта без окна: иначе
 * «назад» после закрытия упирался бы в тот же отчёт ещё раз. Что запись открыта ссылкой отчёта,
 * помнит сама запись истории (`sitesOpened` в её состоянии): после ухода по ссылке лида и «назад»
 * пометка на месте. Окно по присланной ссылке закрывается заменой записи — что было до него, экран
 * не знает.
 */

import { useCallback, useEffect, useMemo, useRef } from 'react';
import {
  NavigationType,
  useLocation,
  useNavigate,
  useNavigationType,
  useSearchParams,
} from 'react-router-dom';

import type { SiteTab, SitesQuery } from './SegmentSitesApi';
import { DECIDER_KEYS, TAB_KEYS } from './SegmentSitesWords';

/** Имена в адресе: чей прогон, вкладка, кто решил, страница. */
const PARAM = { run: 'sites_run', tab: 'sites', by: 'sites_by', page: 'sites_page' } as const;
/** Прогон отчёта — имя вкладки сбора (`leadFilters.ts`). */
const RUN = 'run';
/** Пометка записи истории: окно открыто ссылкой с отчёта без окна — он на шаг назад. */
const OPENED = 'sitesOpened';

export interface SitesView extends SitesQuery {
  open: boolean;
}

const CLOSED: SitesView = { open: false, tab: 'all', decider: null, page: 1 };

function pageOf(raw: string | null): number {
  const value = raw !== null && /^\d{1,6}$/.test(raw) ? Number(raw) : 1;
  return value >= 1 ? value : 1;
}

/** Вид окна из адреса — только если он про этот прогон. */
export function viewOf(params: URLSearchParams, run: number): SitesView {
  if (params.get(PARAM.run) !== String(run)) return CLOSED;
  return {
    open: true,
    tab: TAB_KEYS.find((key) => key === params.get(PARAM.tab)) ?? 'all',
    decider: DECIDER_KEYS.find((key) => key === params.get(PARAM.by)) ?? null,
    page: pageOf(params.get(PARAM.page)),
  };
}

/** Вид → адрес: умолчания не пишутся, закрытое окно — без своих имён вовсе. */
export function written(current: URLSearchParams, run: number, view: SitesView): URLSearchParams {
  const next = new URLSearchParams(current);
  for (const name of Object.values(PARAM)) next.delete(name);
  if (!view.open) return next;
  next.set(RUN, String(run));
  next.set(PARAM.run, String(run));
  if (view.tab !== 'all') next.set(PARAM.tab, view.tab);
  if (view.decider !== null) next.set(PARAM.by, view.decider);
  if (view.page > 1) next.set(PARAM.page, String(view.page));
  return next;
}

/** Открыта ли запись истории ссылкой с отчёта без окна — по её состоянию. */
export function openedHere(state: unknown): boolean {
  return state !== null && typeof state === 'object' && OPENED in state && state[OPENED] === true;
}

const MARK = { [OPENED]: true } as const;

/** Смена вида внутри окна — замена записи истории. */
export type SitesGo = (patch: Partial<SitesView>) => void;

export interface SitesWindow {
  view: SitesView;
  go: SitesGo;
  /** «Закрыть» и Esc: шаг назад к отчёту без окна — или замена записи. */
  close: () => void;
  /** Адрес окна на вкладке — у ссылок, которые его открывают. */
  href: (tab: SiteTab) => string;
}

export function useSitesWindow(run: number): SitesWindow {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const type = useNavigationType();
  const navigate = useNavigate();
  const view = useMemo(() => viewOf(params, run), [params, run]);
  const opened = openedHere(location.state);

  // Окно открыто ссылкой с отчёта без окна (переход вперёд с прошлой отрисовки этого же отчёта) —
  // запись помечается: «Закрыть» вернётся на шаг назад. Открытое по ссылке снаружи — не помечается.
  const before = useRef<boolean | null>(null);
  useEffect(() => {
    const was = before.current;
    before.current = view.open;
    if (view.open && was === false && type === NavigationType.Push && !opened) {
      void navigate({ search: location.search }, { replace: true, state: MARK });
    }
  }, [view.open, location.key, location.search, type, opened, navigate]);

  // Замена записи — с её пометкой: иначе «Закрыть» после смены вкладки забыло бы, куда вернуться.
  const go = useCallback<SitesGo>(
    (patch) =>
      setParams((current) => written(current, run, { ...viewOf(current, run), ...patch }), {
        replace: true,
        state: opened ? MARK : undefined,
      }),
    [opened, run, setParams],
  );

  const close = useCallback(() => {
    if (opened) void navigate(-1);
    else setParams((current) => written(current, run, CLOSED), { replace: true });
  }, [opened, navigate, run, setParams]);

  const href = useCallback(
    (tab: SiteTab) =>
      `${location.pathname}?${written(params, run, { ...CLOSED, open: true, tab }).toString()}`,
    [location.pathname, params, run],
  );

  return { view, go, close, href };
}
