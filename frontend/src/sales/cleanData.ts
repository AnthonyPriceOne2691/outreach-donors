/**
 * Очистка лидов гипотезы с экрана: ход нажатия, память задачи и итог задачи словами.
 *
 * **Перед запуском — вопрос серверу** (`GET /sales/clean`): платная ли проверка адресов и
 * сколько лидов ждут — на момент нажатия. Платная — окно расхода с этим числом, выдуманная —
 * сразу в очередь (`POST /sales/clean`).
 *
 * **Номер задачи экран помнит по гипотезе**: идущая очистка переживает перезагрузку, а
 * кончившуюся экран забывает — её итог виден, пока экран открыт, и не висит на вкладке
 * лидов неделю (столько очередь хранит итог задачи).
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { cleanSalesLeads, readSalesClean } from '../api/sales';
import { leadReasonTitle } from '../api/salesLabels';
import type { SalesCleanReport, SalesCleanView } from '../api/salesTypes';
import { formatNumber } from '../format';
import { jobRestarted } from '../jobs/JobLine';
import { notify } from '../notices';
import { forget, remember, remembered } from '../storage';

/** Ключ всего раздела: после очистки устаревают и лиды, и счётчики гипотез. */
const SALES_QUERIES = ['sales'] as const;

/** Где экран помнит номер очистки гипотезы. */
export function cleanJobKey(hypothesis: number): string {
  return `sales:last-clean:${hypothesis}`;
}

/** Ход очистки гипотезы: вопрос серверу, окно расхода, постановка, память задачи. Отказ —
 *  одним местом: в окне, пока оно открыто (`startError`), иначе под кнопкой (`refusal`). */
export function useCleaning(hypothesis: number) {
  const client = useQueryClient();
  const key = cleanJobKey(hypothesis);
  const [jobId, setJobId] = useState<string | null>(() => remembered(key));
  const [paid, setPaid] = useState<SalesCleanView | null>(null);
  const start = useMutation({
    mutationFn: () => cleanSalesLeads({ hypothesis_id: hypothesis }),
    onSuccess: (queued) => {
      setPaid(null);
      setJobId(queued.job_id);
      remember(key, queued.job_id);
      // Номер очистки гипотезы постоянный: строка с ним уже знает исход прежней очистки и
      // сама больше не спрашивает — без этого показала бы прежний итог.
      void jobRestarted(client, queued.job_id);
      notify({ message: 'Очистка ушла в очередь задач', color: 'green' });
    },
  });
  const ask = useMutation({
    mutationFn: () => readSalesClean(hypothesis),
    onSuccess: (view) => {
      if (view.paid) setPaid(view);
      else start.mutate();
    },
  });
  return {
    jobId,
    /** Сервер сказал «платно» и назвал число — открыто окно расхода. */
    paid,
    /** Нажатие ждёт сервера без окна; с окном ждёт кнопка окна (`starting`). */
    busy: ask.isPending || (start.isPending && paid === null),
    starting: start.isPending,
    startError: start.error,
    refusal: ask.error ?? (paid === null ? start.error : null),
    press: () => {
      start.reset();
      ask.mutate();
    },
    confirm: () => start.mutate(),
    /** Пока постановка идёт, окно не закрывается: её отказ иначе не увидели бы нигде. */
    cancel: () => {
      if (start.isPending) return;
      start.reset();
      setPaid(null);
    },
    /** Задача кончилась: лиды и счётчики — заново, номер — забыть. */
    finished: () => {
      forget(key);
      void client.invalidateQueries({ queryKey: SALES_QUERIES });
    },
  };
}

/** Отказы — словами фильтра лидов, частые первыми: «дубль — 2, негодный адрес — 1». */
function reasonsOf(rejected: Record<string, number>): string {
  const named = Object.entries(rejected)
    .filter(([, count]) => count > 0)
    .sort(([, a], [, b]) => b - a)
    .map(([code, count]) => `${leadReasonTitle(code)} — ${formatNumber(count)}`);
  return named.length > 0 ? ` (${named.join(', ')})` : '';
}

/** Итог очистки одной строкой — из отчёта задачи: сколько проверено и куда ушло, сколько
 *  проверок стоили денег и почему платная часть остановилась, если остановилась. */
export function cleanLine(report: Record<string, unknown>): string {
  const found = report as Partial<SalesCleanReport>;
  const checked = found.checked ?? 0;
  if (checked === 0) return 'Лидов, ждущих очистки, не было — проверять нечего.';
  const rejected = found.rejected ?? {};
  const total = Object.values(rejected).reduce((sum, count) => sum + count, 0);
  const unverified = found.unverified ?? 0;
  const parts = [
    `Проверено ${formatNumber(checked)}: готово ${formatNumber(found.ready ?? 0)}, ` +
      `отклонено ${formatNumber(total)}${reasonsOf(rejected)}, ` +
      `не проверено ${formatNumber(unverified)}.`,
  ];
  const paid = found.paid_units ?? 0;
  if (paid > 0) parts.push(`Платных проверок адреса: ${formatNumber(paid)}.`);
  if (typeof found.stopped === 'string') {
    parts.push(
      `Платная часть остановлена: ${found.stopped} — непроверенные остались новыми, ` +
        'повторите очистку, когда причина снята.',
    );
  } else if (unverified > 0) {
    parts.push('Непроверенные остались новыми — следующая очистка повторит их.');
  }
  return parts.join(' ');
}
