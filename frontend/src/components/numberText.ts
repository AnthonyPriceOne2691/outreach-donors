/**
 * Число в поле ввода — строкой, как набрано, и разбор её по правилу поля.
 *
 * **Поле держит набранное, а не число.** `NumberInput` Mantine разбирает набор
 * на лету и молча выбрасывает то, что не цифра: в целом поле «1.5» становилось
 * 15, «1e3» — 13, «-5» — 5, а «12.» в денежном поле приходило строкой, экран
 * делал из неё «пусто» и стирал поле посреди набора (проверка QA 10.10.2026:
 * «Не дороже, $» — «12.5» → 5; «Собрать из прогона №» — «1.5» → 15, и сервер
 * честно отвечал «Прогона №15 нет»). Теперь в поле остаётся то, что набрали,
 * а что с ним не так, говорит отказ под полем — до нажатия. Число из строки
 * достают тогда, когда оно нужно: при отправке.
 *
 * **Догадок нет.** Разряды — пробелом, дробь — после точки или запятой, и
 * только так: «1.000.000» и «12ю5» — отказ, а не угаданное число (тот же довод
 * у цены донора, `donors/PriceForm`).
 */

import { formatNumber } from '../format';

/** Правило поля: целое или с центами и границы включительно. */
export interface NumberRule {
  /** Знаков после запятой: 0 — только целое, 2 — деньги. */
  decimals: 0 | 2;
  min: number;
  /** Нет — сверху поле не ограничено. */
  max?: number;
}

/** Пробелы между разрядами — любые: обычный, неразрывный, узкий. */
const SPACES = /\s/g;

/** Число, как его пишут: цифры, дробь — после точки или запятой. Разделитель
 *  в конце — середина набора: «12.» на пути к «12.5» — уже 12. */
const WRITTEN = /^-?(?:\d+(?:[.,]\d*)?|[.,]\d+)$/;

/** Целое, как его пишут: только цифры, минус — чтобы «-5» получил отказ границей. */
const WHOLE = /^-?\d+$/;

function bare(text: string): string {
  return text.replace(SPACES, '');
}

/** Значение записанного числа: запятая — та же точка. */
function valueOf(digits: string): number {
  return Number(digits.replace(',', '.'));
}

/** Число из набранного; `null` — пусто или не число. */
export function numberOf(text: string): number | null {
  const digits = bare(text);
  return WRITTEN.test(digits) ? valueOf(digits) : null;
}

/** «от 0 до 10 000 000» — числами по-русски. */
export function rangeText(range: { min: number; max: number }): string {
  return `от ${formatNumber(range.min)} до ${formatNumber(range.max)}`;
}

/** Отказ по виду: целое поле не берёт ни дроби, ни букв, денежное — больше
 *  двух знаков после запятой. */
function shapeRefusal(digits: string, rule: NumberRule): string | null {
  if (rule.decimals === 0) {
    if (WHOLE.test(digits)) return null;
    return rule.max === undefined
      ? 'Только целое число'
      : `Только целое число ${rangeText({ min: rule.min, max: rule.max })}`;
  }
  if (!WRITTEN.test(digits)) return 'Только число, например 99,50';
  const fraction = digits.split(/[.,]/)[1] ?? '';
  return fraction.length > rule.decimals ? 'Не больше двух знаков после запятой' : null;
}

/** Отказ по величине — словами, какие границы. */
function rangeRefusal(value: number, rule: NumberRule): string | null {
  if (rule.max === undefined) {
    return value < rule.min ? `Не меньше ${formatNumber(rule.min)}` : null;
  }
  return value < rule.min || value > rule.max
    ? `Допустимо ${rangeText({ min: rule.min, max: rule.max })}`
    : null;
}

/**
 * Почему набранное не годится — словами; `null` — годится. Пустое поле — не
 * отказ этой функции: где-то это «без предела», где-то «впишите число», и
 * решает экран.
 */
export function numberRefusal(text: string, rule: NumberRule): string | null {
  const digits = bare(text);
  if (digits === '') return null;
  return shapeRefusal(digits, rule) ?? rangeRefusal(valueOf(digits), rule);
}

/** Число из поля, если оно годится по правилу; пусто или с отказом — `null`. */
export function validNumber(text: string, rule: NumberRule): number | null {
  return numberRefusal(text, rule) === null ? numberOf(text) : null;
}

const SHOWN: Record<NumberRule['decimals'], Intl.NumberFormat> = {
  0: new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }),
  2: new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }),
};

/** Число в поле — как на остальных экранах: разряды пробелом, дробь после
 *  запятой. Пробел обычный, а не неразрывный, как у `formatNumber`: в поле его
 *  стирают и набирают руками. */
export function numberText(value: number, decimals: NumberRule['decimals'] = 0): string {
  return SHOWN[decimals].format(value).replace(SPACES, ' ');
}

/** Одно ли число в двух полях — по смыслу, а не по написанию: «1 250,5» и
 *  «1250.50» — одно. Не числа сравниваются как набраны. */
export function sameNumber(left: string, right: string): boolean {
  const a = numberOf(left);
  const b = numberOf(right);
  return a === null || b === null ? left.trim() === right.trim() : a === b;
}
