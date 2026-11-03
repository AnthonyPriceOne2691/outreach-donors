/**
 * Какой этап каким правом открыт — на экране.
 *
 * Сервер отказывает сам: в общих списках и числах этапа без права нет, а письмо или
 * переписка этапа по номеру — 403 словами (`access/permissions.py`). Экран не предлагает
 * того, в чём сервер откажет. Своим файлом, а не в `stages.ts`: права лежат в `types.ts`,
 * а тот сам берёт этап из `stages.ts`.
 */

import type { Stage } from './stages';
import type { Permission } from './types';

/** Что учётке можно — `can` сессии (`auth/AuthProvider`). */
export type Can = (permission: Permission) => boolean;

/** Этап → право, которое его открывает. Копия `STAGE_RIGHTS` сервера, сверка —
 *  `tests/test_access_roles.py`. */
export const STAGE_RIGHTS: Partial<Record<Stage, Permission>> = {
  donors: 'donors',
  advertisers: 'advertisers',
  sales: 'sales',
};

/** Этап на общих экранах — только тому, кто его видит: у каждого этапа своё право. */
export function stageShown(stage: string, can: Can): boolean {
  const right = STAGE_RIGHTS[stage as Stage];
  return right === undefined || can(right);
}

/** Состояния диалога, которые бывают только у одного этапа. */
const STATE_STAGES: Partial<Record<string, Stage>> = {
  sales_pending: 'sales',
  lead: 'advertisers',
  lead_taken: 'advertisers',
};

/** Состояние диалога на экране: состояния этапа — тоже только с его правом. */
export function stateShown(state: string, can: Can): boolean {
  const stage = STATE_STAGES[state];
  return stage === undefined || stageShown(stage, can);
}
