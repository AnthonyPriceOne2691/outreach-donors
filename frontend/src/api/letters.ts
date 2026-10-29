import type {
  BuildLettersRequest,
  BuildQueued,
  LetterStage,
  LettersView,
  QueuedLetter,
  SendResult,
} from './types';
import type { QueueAutosend } from './autosend';
import { request } from './client';
import type { Stage } from './stages';
import type { ResolveOutcome, ResolvedLetter, UnknownLettersView } from './unknownOutcome';

/** Аудитория Этапа 2: по найденной ссылке или бизнесы ниши из выдачи (свой оффер, своя
 *  очередь и своя пачка). Здесь, а не в `types.ts`: тот у потолка длины. */
export type LetterAudience = 'links' | 'niche';

/** Сборка с аудиторией Этапа 2. Нет — по найденной ссылке; `niche` у доноров сервер не примет. */
export interface BuildRequest extends BuildLettersRequest {
  audience?: LetterAudience;
}

/** Этап и аудитория в адресе. Умолчания сервера — доноры и «по ссылке» — без параметров:
 *  так экран спрашивал до аудиторий, и прежние адреса не меняются. */
function scopeOf(stage: LetterStage, audience: LetterAudience): string {
  if (stage === 'donors') return '';
  return audience === 'links' ? `?stage=${stage}` : `?stage=${stage}&audience=${audience}`;
}

/** Очередь этапа и аудитории. */
export function listLetters(
  stage: LetterStage = 'donors',
  audience: LetterAudience = 'links',
): Promise<LettersView> {
  return request<LettersView>(`/letters${scopeOf(stage, audience)}`);
}

export function buildLetters(body: BuildRequest): Promise<BuildQueued> {
  return request<BuildQueued>('/letters/build', { method: 'POST', body });
}

export function editLetter(
  id: number,
  body: { subject: string; body: string },
): Promise<QueuedLetter> {
  return request<QueuedLetter>(`/letters/${id}`, { method: 'PATCH', body });
}

export function skipLetter(id: number): Promise<QueuedLetter> {
  return request<QueuedLetter>(`/letters/${id}/skip`, { method: 'POST' });
}

export function sendLetter(id: number): Promise<SendResult> {
  return request<SendResult>(`/letters/${id}/send`, { method: 'POST' });
}

/** Пачка ушла в очередь задач: сколько писем ждёт в очереди этапа и уходит ли очередь сама
 *  и дальше. */
export interface SendQueueQueued {
  job_id: string;
  queued: number;
  autosend?: boolean;
}

/** Отправить всю очередь этапа пачкой (слово Anthony 06.10.2026) — одной аудитории: пачка
 *  бизнесов ниши письма по найденной ссылке не берёт, и наоборот. «По ссылке» — без поля,
 *  как до аудиторий. `keepSending` — отметка «дальше — сама»: остаток очередь отправит сама
 *  (`letters/autosend.py`); не названа — поля нет, и режим не включается. */
export function sendQueue(
  stage: Stage,
  audience: LetterAudience = 'links',
  keepSending?: boolean,
): Promise<SendQueueQueued> {
  const body: Record<string, unknown> = audience === 'links' ? { stage } : { stage, audience };
  if (keepSending !== undefined) body.keep_sending = keepSending;
  return request<SendQueueQueued>('/letters/send-queue', { method: 'POST', body });
}

/** Очередь этапа и аудитории больше не уходит сама: письма остаются в очереди. */
export function stopAutosend(
  stage: Stage,
  audience: LetterAudience = 'links',
): Promise<QueueAutosend> {
  const body = audience === 'links' ? { stage } : { stage, audience };
  return request<QueueAutosend>('/letters/send-queue/stop', { method: 'POST', body });
}

/** Письма этапа и аудитории с неизвестным исходом: зависли в «отправляется» дольше пяти
 *  минут. */
export function listUnknownLetters(
  stage: LetterStage = 'donors',
  audience: LetterAudience = 'links',
): Promise<UnknownLettersView> {
  return request<UnknownLettersView>(`/letters/unknown${scopeOf(stage, audience)}`);
}

/** Решение человека по журналу платформы: «ушло» или «вернуть в очередь». */
export function resolveLetter(id: number, outcome: ResolveOutcome): Promise<ResolvedLetter> {
  return request<ResolvedLetter>(`/letters/${id}/resolve`, { method: 'POST', body: { outcome } });
}
