/**
 * Воронка продаж (`GET /sales/funnel`): лидов на каждом шаге пути — по гипотезам и итогом.
 * Своим файлом: `salesTypes.ts` стоит у предела длины.
 */

/** Лидов на каждом шаге воронки (`GET /sales/funnel`): имена — шаги, в порядке пути лида. */
export interface FunnelCounts {
  /** Письмо цепочки ждёт отправки, ни одно ещё не ушло. */
  queued: number;
  /** Первое письмо ушло в период — лидов, а не писем. */
  sent: number;
  /** Письмо дошло, и ни одно не вернулось. */
  delivered: number;
  /** Вернулось хоть одно письмо цепочки — отказ сильнее доставки. */
  bounced: number;
  /** Ответил человек или попросил не писать; автоответ — не ответ. */
  answered: number;
  /** Передача телемаркетологу заведена. */
  handed_off: number;
}

/** Воронка одной гипотезы. */
export interface FunnelRow {
  hypothesis_id: number;
  name: string;
  counts: FunnelCounts;
}

/** Экран воронки: строки по гипотезам, старшие первыми, и итог по ним. */
export interface SalesFunnelView {
  /** Гипотеза фильтра; `null` — все. */
  hypothesis_id: number | null;
  /** Период — как спросили: моменты ISO с поясом, `until` не включая. */
  since: string | null;
  until: string | null;
  rows: FunnelRow[];
  total: FunnelCounts;
}
