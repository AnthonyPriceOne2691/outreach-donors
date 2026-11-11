/**
 * Проверка адреса перед первым письмом — числами экрана «Письма» (11.11.2026).
 *
 * Найденный адрес шёл в письмо сразу, и первая большая пачка вернула отказы «такого ящика
 * нет» — ящик рассылки встал на автопаузу. Теперь сборка очереди сначала спрашивает, существует
 * ли адрес (сервер: `letters/address_gate.py`), и экран говорит, что из этого вышло: у скольких
 * адрес проверен, сколько ждут проверки, у скольких проверить нельзя и у скольких адреса нет.
 * Числа — словами сервера в той же воронке; проверка этапа выключена — сервер их не шлёт, и
 * экран о ней молчит.
 */

import { formatNumber } from '../format';

type Funnel = Record<string, number>;

/** Ступень воронки и заметки — словами сервера (`address_gate.py`). */
export const CHECKED_STEP = 'адрес проверен';
const UNVERIFIABLE = 'не проверить';
const GONE = 'адрес не существует';
const WAITING = 'ждут проверки адреса';

const NBSP = ' ';

export interface CheckCounts {
  /** Письмо соберётся только после проверки: она идёт при сборке очереди. */
  waiting: number;
  /** Адрес проверен — письмо соберётся без нового вопроса. */
  checked: number;
  /** Из проверенных — сервер не говорит, есть ли ящик: письмо собирается. */
  unverifiable: number;
  /** Адресаты, выпавшие до первого письма: ящика нет, другого адреса тоже. */
  gone: number;
}

/** Числа проверки; `null` — сервер их не прислал: проверка этапа выключена (и у бизнесов ниши
 *  воронка своя). */
export function checkCounts(funnel: Funnel): CheckCounts | null {
  const checked = funnel[CHECKED_STEP];
  if (checked === undefined) return null;
  return {
    waiting: funnel[WAITING] ?? 0,
    checked,
    unverifiable: funnel[UNVERIFIABLE] ?? 0,
    gone: funnel[GONE] ?? 0,
  };
}

/** « · адрес проверен у 250» — продолжение строки «Ещё не писали»: сколько из них письмо получат
 *  без нового вопроса. Нуля не говорит: до первой сборки он читался бы отказом. */
export function checkedHint(funnel: Funnel): string {
  const counts = checkCounts(funnel);
  if (counts === null || counts.checked === 0) return '';
  return ` · адрес проверен у${NBSP}${formatNumber(counts.checked)}`;
}

/** Что стоит рядом с «ждут проверки адреса»: чего проверка не смогла и кого она сняла. */
export function checkTail(counts: CheckCounts): string[] {
  const said: string[] = [];
  if (counts.unverifiable > 0)
    said.push(`не проверить:${NBSP}${formatNumber(counts.unverifiable)}`);
  if (counts.gone > 0) said.push(`адрес не существует:${NBSP}${formatNumber(counts.gone)}`);
  return said;
}

/** Есть ли о чём сказать строкой проверки: ждущие, непроверяемые или выпавшие. */
export function checkWorthSaying(counts: CheckCounts | null): counts is CheckCounts {
  return counts !== null && counts.waiting + counts.unverifiable + counts.gone > 0;
}

/** Пояснение в «i» строки проверки — только о числах, которые в ней стоят. */
export function checkHint(counts: CheckCounts): string {
  const said = [
    'Перед первым письмом сервис спрашивает, существует ли ящик. Проверка идёт при сборке ' +
      'очереди, до текста письма: нажмите «Собрать очередь».',
  ];
  if (counts.unverifiable > 0) {
    said.push(
      '«Не проверить» — почтовый сервер не говорит, есть ли ящик: таким письмо собирается.',
    );
  }
  if (counts.gone > 0) {
    said.push(
      '«Адрес не существует» — ящика нет, а другого адреса у сайта не нашлось: письмо не ' +
        'собирается, сайт ждёт нового адреса.',
    );
  }
  return said.join(' ');
}

/** Слова итога сборки в том порядке, в каком их читают: что вышло, потом чего не вышло. */
const REPORT_ORDER = [
  'существует',
  'проверены раньше',
  'проверить нельзя',
  'не существует',
  'не проверено',
];

/** Итог сборки про адреса — строкой под «Сборка писем: готово»: сколько проверено и чем
 *  кончилось, а если проверка не состоялась — почему письма этим адресам не собраны. `null` —
 *  проверки не было (этап выключен или собирать было некого). */
export function buildCheckLine(report: Record<string, unknown>): string | null {
  const counts = report.address_check;
  if (typeof counts !== 'object' || counts === null) return null;
  const found = counts as Record<string, unknown>;
  const countOf = (name: string): number => {
    const value = found[name];
    return typeof value === 'number' ? value : 0;
  };
  const said = REPORT_ORDER.filter((name) => countOf(name) > 0).map(
    (name) => `${name}${NBSP}${formatNumber(countOf(name))}`,
  );
  if (said.length === 0) return null;
  const why =
    typeof report.unchecked === 'string' && report.unchecked !== '' ? report.unchecked : null;
  const tail = why === null ? '' : ` Непроверенным письма не собраны — ${why}.`;
  return `Адреса перед письмом: ${said.join(', ')}.${tail}`;
}
