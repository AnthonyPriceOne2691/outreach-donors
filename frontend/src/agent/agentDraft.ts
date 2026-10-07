/**
 * Черновик настроек агента на экране: поля как их набирают и проверка
 * тем же правилом, что у схемы сервера (`api/agent/schemas.py`), — до
 * нажатия, а не отказом после.
 *
 * Доводы и темы набираются по одному на строку: так их правят как текст,
 * а не кнопками «добавить пункт». На сервер уходят строки без пустых.
 */

import type { AgentSettingsBody } from '../api/agent';
import { formatNumber } from '../format';

/** Границы — те же, что у схемы сервера. */
export const LIMITS = { goal: 2000, tone: 500, line: 300, lines: 20, price: 100_000 } as const;

export interface AgentDraft {
  enabled: boolean;
  goal: string;
  tone: string;
  points: string;
  /** Как набрано в поле: пусто — предела нет. */
  price: number | '';
  stopTopics: string;
  /** Режим и предел ответов автопилота — как пришли: полей правки нет, терять нельзя. */
  mode: AgentSettingsBody['mode'];
  maxTurns: AgentSettingsBody['max_turns'];
}

export type DraftField = 'goal' | 'tone' | 'points' | 'price' | 'stopTopics';

export function draftOf(body: AgentSettingsBody): AgentDraft {
  return {
    enabled: body.enabled,
    goal: body.goal,
    tone: body.tone,
    points: body.points.join('\n'),
    price: body.price_limit_usd === null ? '' : Number(body.price_limit_usd),
    stopTopics: body.stop_topics.join('\n'),
    mode: body.mode,
    maxTurns: body.max_turns,
  };
}

export function linesOf(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '');
}

function textRefusal(text: string, limit: number): string | null {
  if (text.trim() === '') return 'Пусто — напишите хотя бы фразу';
  if (text.trim().length > limit) return `Длиннее ${limit} знаков`;
  return null;
}

function linesRefusal(text: string): string | null {
  const lines = linesOf(text);
  if (lines.length > LIMITS.lines) return `Пунктов ${lines.length} — больше ${LIMITS.lines}`;
  const long = lines.findIndex((line) => line.length > LIMITS.line);
  return long === -1 ? null : `Строка ${long + 1} длиннее ${LIMITS.line} знаков`;
}

/** Что не так с полями черновика — словами, по полю. */
export function refusalsOf(draft: AgentDraft): Partial<Record<DraftField, string>> {
  const found: Partial<Record<DraftField, string | null>> = {
    goal: textRefusal(draft.goal, LIMITS.goal),
    tone: textRefusal(draft.tone, LIMITS.tone),
    points: linesRefusal(draft.points),
    stopTopics: linesRefusal(draft.stopTopics),
    price:
      draft.price !== '' && (draft.price < 0 || draft.price > LIMITS.price)
        ? `Допустимо от 0 до ${formatNumber(LIMITS.price)}`
        : null,
  };
  return Object.fromEntries(
    Object.entries(found).filter((entry): entry is [DraftField, string] => entry[1] !== null),
  );
}

/** Тело запроса из черновика; `null` — в черновике есть отказ. */
export function bodyOf(draft: AgentDraft): AgentSettingsBody | null {
  if (Object.keys(refusalsOf(draft)).length > 0) return null;
  return {
    enabled: draft.enabled,
    goal: draft.goal.trim(),
    tone: draft.tone.trim(),
    points: linesOf(draft.points),
    price_limit_usd: draft.price === '' ? null : draft.price.toFixed(2),
    stop_topics: linesOf(draft.stopTopics),
    mode: draft.mode,
    max_turns: draft.maxTurns,
  };
}

/** Совпадает ли черновик с действующими — по смыслу, а не по пробелам. */
export function sameDraft(draft: AgentDraft, inUse: AgentSettingsBody): boolean {
  const asIs = draftOf(inUse);
  return (
    draft.enabled === asIs.enabled &&
    draft.goal.trim() === asIs.goal &&
    draft.tone.trim() === asIs.tone &&
    linesOf(draft.points).join('\n') === asIs.points &&
    draft.price === asIs.price &&
    linesOf(draft.stopTopics).join('\n') === asIs.stopTopics &&
    draft.mode === asIs.mode &&
    draft.maxTurns === asIs.maxTurns
  );
}
