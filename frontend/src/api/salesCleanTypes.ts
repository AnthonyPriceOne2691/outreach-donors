/**
 * Очистка лидов гипотезы (`/sales/clean`): сколько лидов её ждёт, платная ли проверка адресов
 * и итог задачи. Своим файлом: `salesTypes.ts` стоит у предела длины.
 */

/** Перед очисткой лидов гипотезы (`GET /sales/clean`): сколько их ждёт и платная ли проверка. */
export interface SalesCleanView {
  hypothesis_id: number;
  /** Лидов «новый» — их возьмёт очистка; это и потолок платных проверок адреса. */
  waiting: number;
  /** Проверка адресов живая (Hunter): перед запуском — окно подтверждения расхода. */
  paid: boolean;
}

/** Очистить лидов гипотезы, которые ждут очистки. */
export interface SalesCleanBody {
  hypothesis_id: number;
}

/** Итог очистки — отчёт задачи (`clean_jobs.report_of` сервера). */
export interface SalesCleanReport {
  checked: number;
  ready: number;
  /** Код причины отказа → сколько лидов; слова — те же, что у фильтра лидов. */
  rejected: Record<string, number>;
  /** Остались «новыми»: проверка адреса не выполнена, следующая очистка повторит. */
  unverified: number;
  /** Доменов, по которым DNS не ответил: адреса прошли дальше непроверенными. */
  mx_unknown: number;
  verified: number;
  /** Сколько проверок адреса стоили денег. */
  paid_units: number;
  verifier: string;
  /** Платная часть остановлена (квота, закрытая учётка) — причина словами. */
  stopped: string | null;
}
