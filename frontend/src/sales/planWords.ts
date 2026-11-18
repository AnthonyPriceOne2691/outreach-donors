/**
 * Слова колонки «План работ» и строки плана: этап гипотезы плана, «найдено N из цели», расход.
 *
 * Этап — словами человека, а не кодом конвейера (`plan_models.PlanStage`): сбор называет глубину
 * волны и сколько ключей она уже купила — «сбор: топ-20, ключи 1 200 из 30 000». Ключи — ключ ×
 * рынок, как их считает реестр: лист на двух рынках покупает каждый ключ дважды.
 *
 * Деньги — правилом денег сервиса (`formatUsd`) с неразрывным пробелом перед знаком (`dollars`),
 * числа — с разрядами (`formatNumber`).
 */

import { formatNumber, plural } from '../format';
import type { PlanLive, PlanRow } from './planApi';
import { dollars, topWords } from './PlanPreviewView';

/** Последняя ступень «Глубины поиска до» книги: волна 4 покупает страницы 4–5 одной покупкой. */
const LAST_STEP = 5;

/** Страница, докуда покупает волна гипотезы плана: 1, 2, 3 — сама страница, 4 — до пятой, не
 *  глубже предела гипотезы. */
export function waveDepth(row: Pick<PlanRow, 'wave_page' | 'max_pages'>): number {
  const last = row.wave_page >= 4 ? LAST_STEP : row.wave_page;
  return Math.min(last, row.max_pages);
}

const STAGES: Record<string, string> = {
  waiting: 'ждёт очереди',
  sending: 'письма',
  done: 'готово',
  stopped: 'остановлена',
};

/** Этап словами: «сбор: топ-20, ключи 1 200 из 30 000», «письма», «готово», «остановлена». */
export function stageWords(row: PlanRow): string {
  if (row.stage !== 'collecting') return STAGES[row.stage] ?? row.stage;
  const keys = `ключи ${formatNumber(row.wave_bought)} из ${formatNumber(row.keywords)}`;
  return `сбор: ${topWords(waveDepth(row))}, ${keys}`;
}

/** «найдено 1 240 из 5 000». */
export function foundWords(row: Pick<PlanRow, 'found' | 'target'>): string {
  return `найдено ${formatNumber(row.found)} из ${formatNumber(row.target)}`;
}

/** Доля найденного от цели для полосы — целыми процентами, 0…100. */
export function foundShare(row: Pick<PlanRow, 'found' | 'target'>): number {
  return row.target > 0 ? Math.min(100, Math.round((row.found / row.target) * 100)) : 0;
}

/** «расход 12,40 $ из 50,00 $». */
export function spendWords(spent: number, cap: number): string {
  return `расход ${dollars(spent)} из ${dollars(cap)}`;
}

/** Строка плана: «План «…»: готово 3 пилота из 10 · расход 12,40 $ из 50,00 $». */
export function planWords(plan: PlanLive): string {
  const done = plan.hypotheses.filter((row) => row.stage === 'done').length;
  const total = plan.hypotheses.length;
  const spent = plan.hypotheses.reduce((sum, row) => sum + row.spent_usd, 0);
  const pilots = plural(done, 'пилот', 'пилота', 'пилотов');
  return `План «${plan.name}»: готово ${formatNumber(done)} ${pilots} из ${formatNumber(total)} · ${spendWords(spent, plan.cap_usd)}`;
}

/** Ждут ли гипотезу ключи: её остановили «нужны ключи». */
export function needsKeys(row: PlanRow): boolean {
  return row.problems.some((item) => item.code === 'keys');
}
