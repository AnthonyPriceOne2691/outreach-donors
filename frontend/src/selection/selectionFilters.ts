/**
 * Вкладка, фильтры отбора и страница — в адресе экрана (26.09.2026).
 *
 * **Адрес, а не состояние компонента** — как у доноров и диалогов. Человек
 * обновляет вкладку браузера, возвращается «назад», присылает ссылку
 * коллеге — и видит ту же вкладку с теми же фильтрами и той же страницей,
 * а не первую страницу принятых.
 *
 * **Адрес — чужой ввод.** Его правят руками и присылают устаревшим, поэтому
 * он разбирается строго: незнакомое значение — «не сужать», а не отказ
 * сервера и не пустая таблица без объяснения.
 *
 * **Чего на вкладке не бывает по её определению, того фильтр не предлагает.**
 * Принятые прошли пороги все до одного — фильтра порогов на этой вкладке
 * нет; «не подходит» и «не продаёт» лежат только в отклонённых: отказ
 * порогов и ответ донора сильнее всего остального. Само правило вкладок —
 * на сервере (`_tab` в `backend/features/donors/selection.py`), здесь —
 * только его следствия для списков. Значение из адреса, которого на вкладке
 * не бывает, читается как «не сужать», и из адреса оно уходит.
 */

import {
  SELECTION_ANSWERS,
  SELECTION_HUMAN,
  SELECTION_JUDGES,
  SELECTION_TABS,
  SELECTION_THRESHOLDS,
} from '../api/labels';
import type { SelectionQuery } from '../api/selection';
import type {
  SelectionAnswer,
  SelectionHuman,
  SelectionJudge,
  SelectionTab,
  SelectionThresholds,
} from '../api/types';
import { formatNumber } from '../format';

export interface SelectionFilters {
  tab: SelectionTab;
  /** По домену или причине. Пусто — не сужать. */
  search: string;
  thresholds: SelectionThresholds | null;
  judge: SelectionJudge | null;
  answer: SelectionAnswer | null;
  human: SelectionHuman | null;
  page: number;
}

export const NO_SELECTION_FILTERS: SelectionFilters = {
  tab: 'accepted',
  search: '',
  thresholds: null,
  judge: null,
  answer: null,
  human: null,
  page: 1,
};

export const SELECTION_TAB_KEYS = Object.keys(SELECTION_TABS) as SelectionTab[];
export const JUDGE_KEYS = Object.keys(SELECTION_JUDGES) as SelectionJudge[];
export const HUMAN_KEYS = Object.keys(SELECTION_HUMAN) as SelectionHuman[];

/** Какие значения порогов на вкладке бывают. */
const THRESHOLDS_ON: Record<SelectionTab, SelectionThresholds[]> = {
  accepted: [],
  review: ['suitable', 'unchecked', 'none'],
  rejected: ['suitable', 'unsuitable', 'unchecked', 'none'],
};

/** Какие ответы донора на вкладке бывают: «не продаёт» — всегда отказ. */
const ANSWERS_ON: Record<SelectionTab, SelectionAnswer[]> = {
  accepted: ['answered', 'none', 'sells', 'free'],
  review: ['answered', 'none', 'sells', 'free'],
  rejected: ['answered', 'none', 'sells', 'free', 'declines'],
};

export function thresholdsOn(tab: SelectionTab): SelectionThresholds[] {
  return THRESHOLDS_ON[tab];
}

export function answersOn(tab: SelectionTab): SelectionAnswer[] {
  return ANSWERS_ON[tab];
}

function known<T extends string>(choices: readonly T[], raw: string | null): T | null {
  return choices.find((choice) => choice === raw) ?? null;
}

function wholeNumber(raw: string | null, min: number, max: number): number | null {
  if (raw === null || !/^\d{1,7}$/.test(raw)) return null;
  const value = Number(raw);
  return value >= min && value <= max ? value : null;
}

/** Фильтры без значений, которых на вкладке не бывает. */
function fitted(filters: SelectionFilters): SelectionFilters {
  return {
    ...filters,
    thresholds: known(THRESHOLDS_ON[filters.tab], filters.thresholds),
    answer: known(ANSWERS_ON[filters.tab], filters.answer),
  };
}

export function readSelectionFilters(params: URLSearchParams): SelectionFilters {
  const tab = known(SELECTION_TAB_KEYS, params.get('tab')) ?? 'accepted';
  return {
    tab,
    search: (params.get('search') ?? '').trim(),
    thresholds: known(THRESHOLDS_ON[tab], params.get('thresholds')),
    judge: known(JUDGE_KEYS, params.get('judge')),
    answer: known(ANSWERS_ON[tab], params.get('answer')),
    human: known(HUMAN_KEYS, params.get('human')),
    page: wholeNumber(params.get('page'), 1, 1_000_000) ?? 1,
  };
}

/** Фильтры → запрос к серверу. Имена те же, что у адреса экрана. */
export function queryOf(filters: SelectionFilters): SelectionQuery {
  const fit = fitted(filters);
  return {
    tab: fit.tab,
    ...(fit.search !== '' ? { search: fit.search } : {}),
    ...(fit.thresholds !== null ? { thresholds: fit.thresholds } : {}),
    ...(fit.judge !== null ? { judge: fit.judge } : {}),
    ...(fit.answer !== null ? { answer: fit.answer } : {}),
    ...(fit.human !== null ? { human: fit.human } : {}),
    ...(fit.page > 1 ? { page: fit.page } : {}),
  };
}

/** Фильтры → адрес. Умолчания не пишутся: `?tab=accepted&page=1` ничего
 *  не сужает. */
export function writeSelectionFilters(filters: SelectionFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(queryOf(filters))) {
    if (key === 'tab' && value === 'accepted') continue;
    params.set(key, String(value));
  }
  return params;
}

/** Сужает ли что-нибудь, кроме вкладки. */
export function isSelectionFiltered(filters: SelectionFilters): boolean {
  const fit = fitted(filters);
  return (
    fit.search !== '' ||
    fit.thresholds !== null ||
    fit.judge !== null ||
    fit.answer !== null ||
    fit.human !== null
  );
}

const ANSWER_SAID: Record<SelectionAnswer, string> = {
  answered: 'донор ответил',
  none: 'донор не отвечал',
  sells: `донор ответил «${SELECTION_ANSWERS.sells}»`,
  free: `донор ответил «${SELECTION_ANSWERS.free}»`,
  declines: `донор ответил «${SELECTION_ANSWERS.declines}»`,
};

const HUMAN_SAID: Record<SelectionHuman, string> = {
  unreviewed: 'человек не смотрел',
  reviewed: 'человек смотрел',
  disagrees: 'человек разошёлся с судьёй',
};

/** Что сужает таблицу — словами: «ничего не нашлось» без условий читается
 *  как «на вкладке пусто». */
export function conditionsOf(filters: SelectionFilters): string[] {
  const fit = fitted(filters);
  const said: string[] = [];
  if (fit.search !== '') said.push(`«${fit.search}» в домене или причине`);
  if (fit.thresholds !== null) said.push(`пороги «${SELECTION_THRESHOLDS[fit.thresholds]}»`);
  if (fit.judge === 'none') said.push(SELECTION_JUDGES.none.title);
  else if (fit.judge !== null) said.push(`вердикт вынес «${SELECTION_JUDGES[fit.judge].title}»`);
  if (fit.answer !== null) said.push(ANSWER_SAID[fit.answer]);
  if (fit.human !== null) said.push(HUMAN_SAID[fit.human]);
  return said;
}

/** Пустая вкладка без фильтров — что на ней бывает и почему сейчас пусто. */
const EMPTY_TAB: Record<SelectionTab, { title: string; detail: string }> = {
  accepted: {
    title: 'Принятых пока нет.',
    detail: 'Сюда попадает домен, прошедший пороги, если судья его не отрезал.',
  },
  review: {
    title: 'Разбирать нечего.',
    detail: 'Судья ни о ком не попросил посмотреть, у всех доноров есть данные.',
  },
  rejected: {
    title: 'Отклонённых нет.',
    detail: 'Все домены прошли пороги, и судья никого не отрезал.',
  },
};

export interface Emptiness {
  title: string;
  detail: string;
  /** Есть ли что сбросить: без фильтров кнопка ничего бы не сделала. */
  resettable: boolean;
}

/**
 * Почему под фильтром пусто — словами. Два случая читаются по-разному:
 * пуста сама вкладка — сужать дальше бессмысленно, и это сказано; пусто
 * только под сочетанием условий — условия и названы.
 */
export function emptinessOf(filters: SelectionFilters, inTab: number): Emptiness {
  const resettable = isSelectionFiltered(filters);
  const tab = SELECTION_TABS[filters.tab].title;
  if (inTab === 0) {
    const empty = EMPTY_TAB[filters.tab];
    return {
      title: empty.title,
      detail: resettable ? `${empty.detail} Фильтры тут ни при чём.` : empty.detail,
      resettable,
    };
  }
  const conditions = conditionsOf(filters);
  return {
    title: 'Под фильтр ничего не попало.',
    detail:
      conditions.length === 0
        ? `На этой странице пусто. На вкладке «${tab}» всего — ${formatNumber(inTab)}.`
        : `Условия: ${conditions.join(', ')}. На вкладке «${tab}» всего — ${formatNumber(inTab)}.`,
    resettable,
  };
}
