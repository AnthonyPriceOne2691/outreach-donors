/**
 * Ход применения советов судьи: экран шлёт порцию за порцией, пока сервер не скажет «осталось ноль».
 *
 * **Порция — запрос** (`api/advertisers/judge_apply.py`): сервер фиксирует её целиком вместе с
 * записью журнала, поэтому обрыв связи или «Остановить» теряют только летевшую порцию. Первая порция
 * начинает партию — имя даёт сервер, следующие идут с ним.
 *
 * **Остановленное продолжается той же партией**: «Применить» после остановки или обрыва шлёт порции
 * с прежним именем, и откатывать потом одну партию, а не две. Отказ сервера имя забывает: партию
 * могли откатить, пока окно было открыто, — следующее нажатие начнёт новую.
 *
 * **Порция без хода — конец**, а не повтор до бесконечности: сервер ничего не применил и ничего не
 * пропустил — дальше он скажет то же самое.
 */

import { useEffect, useRef, useState } from 'react';

import { applyJudgeAdvice } from '../api/advertisersJudgeApply';
import type { JudgeApplied, JudgeBand, JudgePlan } from '../api/advertisersJudgeApply';
import { ApiError, refusalOf } from '../api/client';

/** running — порции идут; done — полоса кончилась; stopped — встали по кнопке; failed — отказ. */
export type RunPhase = 'running' | 'done' | 'stopped' | 'failed';

export interface ApplyRun {
  phase: RunPhase;
  band: JudgeBand;
  /** Пар в плане на старте — знаменатель хода. */
  total: number;
  applied: number;
  /** Пропущены: пару за это время решил человек. */
  decided: number;
  /** Пропущены: строку заменил новый обход. */
  gone: number;
  removed: number;
  stopped: number;
  answered: string[];
  answeredTotal: number;
  left: number;
  /** Отказ сервера словами — у `failed`. */
  error: string | null;
}

function started(band: JudgeBand, plan: JudgePlan): ApplyRun {
  return {
    phase: 'running',
    band,
    total: plan.pairs,
    applied: 0,
    decided: 0,
    gone: 0,
    removed: 0,
    stopped: 0,
    answered: [],
    answeredTotal: 0,
    left: plan.pairs,
    error: null,
  };
}

function after(run: ApplyRun, portion: JudgeApplied): ApplyRun {
  return {
    ...run,
    applied: run.applied + portion.applied,
    decided: run.decided + portion.decided,
    gone: run.gone + portion.gone,
    removed: run.removed + portion.removed,
    stopped: run.stopped + portion.stopped,
    answered: [...run.answered, ...portion.answered],
    answeredTotal: run.answeredTotal + portion.answered_total,
    left: portion.left,
  };
}

/** Сколько пар порция прошла: применила или пропустила. */
function moved(portion: JudgeApplied): number {
  return portion.applied + portion.decided + portion.gone;
}

/** Партия, которую продолжит следующее «Применить» той же полосы. */
interface Resumed {
  band: JudgeBand;
  batch: string;
}

/** Чем кончились порции: до чего дошли, какой партией и на чём оборвались (`null` — не оборвались). */
interface Sent {
  run: ApplyRun;
  batch: string | null;
  failure: unknown;
}

/** Порции подряд, пока есть что применять и не просили встать. Отказ не теряет пройденного:
 *  он возвращается вместе с тем, до чего дошли. */
async function send(
  plan: JudgePlan,
  from: ApplyRun,
  first: string | null,
  halted: () => boolean,
  onStep: (run: ApplyRun) => void,
): Promise<Sent> {
  let run = from;
  let batch = first;
  try {
    while (run.left > 0 && !halted()) {
      const portion = await applyJudgeAdvice(run.band, batch, plan.portion);
      batch = portion.batch;
      run = after(run, portion);
      onStep(run);
      if (moved(portion) === 0) break;
    }
  } catch (failure) {
    return { run, batch, failure };
  }
  return { run, batch, failure: null };
}

/** Итог хода: отказ — словами сервера; встали по кнопке — «остановлено»; иначе — конец полосы. */
function settled(sent: Sent, halted: boolean): ApplyRun {
  if (sent.failure !== null) {
    return { ...sent.run, phase: 'failed', error: refusalOf(sent.failure) };
  }
  return { ...sent.run, phase: sent.run.left > 0 && halted ? 'stopped' : 'done' };
}

/** Чем продолжать: имя партии держится, пока полоса не кончилась. Отказ сервера имя забывает —
 *  партию могли откатить; обрыв связи имя держит. */
function resumable(sent: Sent): Resumed | null {
  if (sent.batch === null || sent.run.left === 0 || sent.failure instanceof ApiError) return null;
  return { band: sent.run.band, batch: sent.batch };
}

export function useJudgeApplyRun(onSettled: () => Promise<unknown>) {
  const [run, setRun] = useState<ApplyRun | null>(null);
  const halted = useRef(false);
  const shown = useRef(true);
  const resumed = useRef<Resumed | null>(null);

  // Окно закрыли посреди хода — порции встают: решать без человека у экрана нельзя.
  useEffect(() => {
    shown.current = true;
    return () => {
      shown.current = false;
      halted.current = true;
    };
  }, []);

  const show = (state: ApplyRun) => {
    if (shown.current) setRun(state);
  };

  async function start(band: JudgeBand, plan: JudgePlan): Promise<void> {
    halted.current = false;
    const from = started(band, plan);
    setRun(from);
    const first = resumed.current?.band === band ? resumed.current.batch : null;
    const sent = await send(plan, from, first, () => halted.current, show);
    resumed.current = resumable(sent);
    show(settled(sent, halted.current));
    await onSettled();
  }

  return {
    run,
    start,
    halt: () => {
      halted.current = true;
    },
  };
}
