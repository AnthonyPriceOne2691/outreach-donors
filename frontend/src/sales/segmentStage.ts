/**
 * Ход прогона сбора словами: этап, «X из Y», сколько идёт и когда отмечался.
 *
 * Тест прогона 14.10: выдача шла шесть минут, а экран стоял «в очереди» без хода; после
 * продолжения нужна была перезагрузка; умерший воркер оставлял прогон «идёт» навсегда.
 * Человек в любой момент видит, что делает прогон, сколько осталось и жив ли он.
 *
 * **Жив ли прогон, решает сервер — по очереди задач** (`run.active`, `run.watch`): строка
 * прогона говорит, где он был, а не жив ли он. Задачи нет или умер её воркер — «остановлен»
 * словами сервера и «Продолжить»; воркеров нет — «Задачу некому взять». У сервера старше хода
 * этих полей нет — тогда по стадии, как раньше.
 *
 * **Продолжение стадию не трогает** (стадия «в очереди» для прохода значила бы «купить
 * выдачу»): готовый или остановленный прогон, чья задача жива, — «в очереди задач», а запись
 * хода у него ещё прошлого прохода и не показывается. Так же — прогон, чья задача ещё не у
 * воркера (`watch.job`): стоит в очереди — «в очереди задач», ждёт повтора — «ждёт повтора
 * задачи», хотя стадия у него — этап, на котором встал прошлый проход.
 */

import { formatNumber, plural } from '../format';
import type { SegmentRunRow, SegmentStage } from './segmentApi';

/** Этапы, в которых прогон «идёт» по своей строке. */
export const GOING: ReadonlySet<SegmentStage> = new Set([
  'queued',
  'serp',
  'judge',
  'contacts',
  'leads',
]);

/** Пульс старше этого — прогон давно не отмечался: так и сказано, полными чернилами. Идущий
 *  проход отмечается каждые 15 с. Словами, а не цветом: янтарь на светлом стекле дал
 *  4,06 : 1 при норме 4,5 (замер стенда 14.10.2026). */
export const QUIET_S = 120;

/** Этап словами — «где прогон сейчас». */
export const STAGE_WORDS: Record<SegmentStage, string> = {
  queued: 'в очереди задач',
  serp: 'покупаем выдачу',
  judge: 'судит судья',
  contacts: 'ищем адреса',
  leads: 'заводим лидов и чистим адреса',
  done: 'готово',
  stopped: 'остановлен',
};

/** Этап для «остановлен на этапе …». */
const STAGE_NAMES: Record<string, string> = {
  serp: 'выдача',
  judge: 'судья',
  contacts: 'адреса',
  leads: 'лиды и очистка',
};

/** Режим покупки выдачи — сколько её ждать. */
const SERP_MODES: Record<string, string> = {
  live: 'быстрый режим, секунды',
  priority: 'приоритет, около минуты',
  standard: 'очередь провайдера, обычно около пяти минут',
};

/** Идёт ли прогон: по ответу сервера (очередь задач), у сервера старше хода — по стадии. */
export function isActive(run: SegmentRunRow): boolean {
  return run.active ?? GOING.has(run.stage);
}

/** Задача продолжения стоит в очереди, а стадия — ещё прошлого прохода. */
function resumed(run: SegmentRunRow): boolean {
  return isActive(run) && (run.stage === 'done' || run.stage === 'stopped');
}

/** Задача прогона ещё не у воркера: стоит в очереди (`queued`) или ждёт повтора (`retry`). */
function waiting(run: SegmentRunRow): 'queued' | 'retry' | null {
  if (!isActive(run)) return null;
  const job = run.watch?.job;
  if (job === 'scheduled') return 'retry';
  if (job === 'queued' || job === 'deferred' || run.stage === 'queued' || resumed(run)) {
    return 'queued';
  }
  return null;
}

/** Сколько секунд прошло с отметки; отметки нет — пусто. */
export function secondsSince(moment: string | null | undefined, now = Date.now()): number | null {
  if (moment === null || moment === undefined || moment === '') return null;
  const at = new Date(moment).getTime();
  return Number.isNaN(at) ? null : Math.max(0, Math.round((now - at) / 1000));
}

/** «12 с назад», «3 мин назад» — как у истории прогонов доноров. */
export function ago(seconds: number): string {
  if (seconds < 90) return `${formatNumber(seconds)} с назад`;
  return `${formatNumber(Math.round(seconds / 60))} мин назад`;
}

/** «меньше минуты», «4 мин», «1 ч 05 мин». */
export function lasting(seconds: number): string {
  if (seconds < 60) return 'меньше минуты';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${formatNumber(minutes)} мин`;
  return `${formatNumber(Math.floor(minutes / 60))} ч ${String(minutes % 60).padStart(2, '0')} мин`;
}

/** «из 11 ключей», «из 1 ключа». */
function outOf(done: number, total: number, forms: [string, string, string]): string {
  return `${formatNumber(done)} из ${formatNumber(total)} ${plural(total, ...forms)}`;
}

/** Ход этапа словами по записи сервера: «покупаем выдачу: готово 2 из 11 ключей». */
function stageCount(stage: string, done: number, total: number | null): string | null {
  if (stage === 'serp') {
    return total === null ? null : `готово ${outOf(done, total, ['ключа', 'ключей', 'ключей'])}`;
  }
  if (stage === 'judge' && total !== null) return outOf(done, total, ['сайта', 'сайтов', 'сайтов']);
  if (stage === 'contacts' && total !== null) {
    return outOf(done, total, ['домена', 'доменов', 'доменов']);
  }
  if (stage === 'leads' && done > 0) return `заведено ${formatNumber(done)}`;
  return null;
}

/** Этап в фразе хода: у судьи и адресов — короче, «судья: 12 из 60 сайтов». */
const COUNTED_WORDS: Record<string, string> = { judge: 'судья', contacts: 'адреса' };

/** Ход идущего этапа по записи сервера; записи этого этапа нет — слова стадии. */
function stageProgress(run: SegmentRunRow): string {
  const progress = run.progress;
  if (!isActive(run) || !progress || progress.stage !== run.stage) return STAGE_WORDS[run.stage];
  const count = stageCount(progress.stage, progress.done ?? 0, progress.total);
  const words = COUNTED_WORDS[progress.stage] ?? STAGE_WORDS[run.stage];
  return count === null ? words : `${words}: ${count}`;
}

/** Где прогон сейчас — одной фразой: этап и его ход, «в очереди задач», «готово». */
export function stageLine(run: SegmentRunRow): string {
  if (resumed(run)) return 'в очереди задач — продолжение';
  const wait = waiting(run);
  if (wait === 'retry') return 'ждёт повтора задачи';
  if (wait === 'queued') return STAGE_WORDS.queued;
  return stageProgress(run);
}

/** Где встал остановленный прогон: «остановлен на этапе «судья»: 40 из 60 сайтов». */
export function stoppedAt(run: SegmentRunRow): string | null {
  const progress = run.progress;
  if (isActive(run) || !progress || (run.stage !== 'stopped' && !run.watch?.stalled)) return null;
  const name = STAGE_NAMES[progress.stage];
  if (name === undefined) return null;
  const count = stageCount(progress.stage, progress.done ?? 0, progress.total);
  return `Остановлен на этапе «${name}»${count === null ? '' : `: ${count}`}.`;
}

/** Режим покупки выдачи словами — пока выдача покупается. */
export function serpMode(run: SegmentRunRow): string | null {
  const mode = run.progress?.mode;
  if (!isActive(run) || run.progress?.stage !== 'serp' || !mode) return null;
  return SERP_MODES[mode] ?? mode;
}

export interface Pulse {
  words: string;
  /** Давно не отмечался — сказано словами и не приглушено. */
  quiet: boolean;
}

/** Сколько идёт проход и когда отмечался — у идущего; у стоящего в очереди — когда поставлен. */
export function pulse(run: SegmentRunRow, now = Date.now()): Pulse | null {
  const wait = waiting(run);
  // Ждёт повтора — когда следующая попытка, говорит строка задачи (`JobLine`).
  if (!isActive(run) || wait === 'retry') return null;
  const alive = secondsSince(run.alive_at, now);
  if (alive === null) return null;
  if (wait === 'queued') return { words: `поставлен ${ago(alive)}`, quiet: false };
  const going = secondsSince(run.progress?.pass_started_at, now);
  const lead = going === null ? '' : `идёт ${lasting(going)} · `;
  const quiet = alive > QUIET_S;
  const said = `${lead}обновлено ${ago(alive)}`;
  return { words: quiet ? `${said} — давно не отмечался` : said, quiet };
}

/** Значок стадии в шапке отчёта: что написано и каким цветом. */
export function stageBadge(run: SegmentRunRow): { title: string; color: string } {
  if (run.watch?.stalled) return { title: STAGE_WORDS.stopped, color: 'yellow' };
  if (isActive(run)) {
    const wait = waiting(run);
    const title =
      wait === 'retry'
        ? 'ждёт повтора'
        : wait === 'queued'
          ? STAGE_WORDS.queued
          : STAGE_WORDS[run.stage];
    return { title, color: 'blue' };
  }
  if (run.stage === 'stopped') return { title: STAGE_WORDS.stopped, color: 'yellow' };
  return { title: STAGE_WORDS[run.stage], color: run.stage === 'done' ? 'green' : 'gray' };
}

/** Прогон в списке прогонов — одной строкой состояния. */
export function runState(run: SegmentRunRow): string {
  if (run.watch?.stalled) return 'остановлен: задачи прогона нет';
  return stageLine(run);
}
