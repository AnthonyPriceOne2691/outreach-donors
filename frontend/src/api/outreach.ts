import type {
  CalibrationView,
  LeadSent,
  LeadTaken,
  Reviewed,
  ReviewPrice,
  SenderCard,
  SendersView,
  SendResult,
  StopEntry,
  StopListView,
  SuppressionReason,
  ThreadCard,
  UnboundView,
} from './types';
import type { AttachmentText, OutgoingFile } from './files';
import type { ThreadView } from './thread';
import { download, request, upload } from './client';
import type { Downloaded } from './client';

export function listSenders(): Promise<SendersView> {
  return request<SendersView>('/senders');
}

export function enableSender(id: number): Promise<SenderCard> {
  return request<SenderCard>(`/senders/${id}/enable`, { method: 'POST' });
}

export function disableSender(id: number, reason: string): Promise<SenderCard> {
  return request<SenderCard>(`/senders/${id}/disable`, { method: 'POST', body: { reason } });
}

/** Калибровка разбора: предложение модели против решения человека. */
export function fetchCalibration(): Promise<CalibrationView> {
  return request<CalibrationView>('/replies/calibration');
}

export function listThreads(): Promise<ThreadCard[]> {
  return request<ThreadCard[]>('/threads');
}

export function fetchThread(id: number): Promise<ThreadView> {
  return request<ThreadView>(`/threads/${id}`);
}

/** Страница ответов без письма, номер с единицы: размер страницы называет сервер. */
export function listUnbound(page: number): Promise<UnboundView> {
  return request<UnboundView>(`/replies/unbound?page=${page}`);
}

/** Подтвердить разбор цены человеком. Его решение сильнее любой
 *  уверенности модели и кладёт цену в карточку донора. */
export function reviewReply(id: number, body: ReviewPrice): Promise<Reviewed> {
  return request<Reviewed>(`/replies/${id}`, { method: 'PATCH', body });
}

/**
 * Файл, присланный донором, — запросом с пропуском, а не ссылкой: простая
 * ссылка заголовка `Authorization` не несёт и получала бы 401. Сервер отдаёт
 * его только на скачивание, и имя файла — в заголовке ответа.
 */
export function downloadAttachment(replyId: number, attachmentId: number): Promise<Downloaded> {
  return download(`/replies/${replyId}/attachments/${attachmentId}`);
}

/**
 * Наш ответ на ответ собеседника — и отправка сразу: тем ящиком, что начал
 * переписку, на адрес, с которого ответили, веткой к его письму.
 */
export function answerReply(
  threadId: number,
  replyId: number,
  body: string,
  fileIds: number[] = [],
): Promise<SendResult> {
  return request<SendResult>(`/threads/${threadId}/answer`, {
    method: 'POST',
    body: { reply_id: replyId, body, file_ids: fileIds },
  });
}

/** Файл к ответу — сразу на сервер: тип и размер проверяет он, отказ — словами
 *  до отправки. Файл ждёт в переписке, пока ответ не уйдёт. */
export function attachFile(threadId: number, file: File): Promise<OutgoingFile> {
  const form = new FormData();
  form.append('file', file);
  return upload<OutgoingFile>(`/threads/${threadId}/files`, form);
}

/** Убрать файл, который ещё не ушёл с письмом. */
export function detachFile(threadId: number, fileId: number): Promise<void> {
  return request<void>(`/threads/${threadId}/files/${fileId}`, { method: 'DELETE' });
}

/** Файл нашего письма — на скачивание. */
export function downloadLetterFile(messageId: number, fileId: number): Promise<Downloaded> {
  return download(`/messages/${messageId}/attachments/${fileId}`);
}

/** Текст вложения ответа — тот, что видела модель разбора цены. */
export function fetchAttachmentText(
  replyId: number,
  attachmentId: number,
): Promise<AttachmentText> {
  return request<AttachmentText>(`/replies/${replyId}/attachments/${attachmentId}/text`);
}

/** Ответ рекламодателя — в работу. Повторно — отказ: лид уже кто-то ведёт. */
export function takeLead(id: number): Promise<LeadTaken> {
  return request<LeadTaken>(`/replies/${id}/lead`, { method: 'POST' });
}

/** Взятый лид — в CRM ещё раз: вебхук настроили позже или получатель лежал. */
export function sendLead(id: number): Promise<LeadSent> {
  return request<LeadSent>(`/replies/${id}/lead/send`, { method: 'POST' });
}

/** Лиды файлом CSV — те же поля, что уходят вебхуком в CRM. Время в файле — как на
 *  экране (`formatDateTime`): в поясе браузера, его имя уходит запросом. До 10.10.2026
 *  файл писал время в UTC машинным видом (проверка прода 10.10.2026). */
export function exportLeads(): Promise<Downloaded> {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return download(`/replies/leads.csv?tz=${encodeURIComponent(zone)}`);
}

export function listSuppressions(): Promise<StopListView> {
  return request<StopListView>('/suppressions');
}

/** Запись, только что заведённая руками, — и легла ли она на домен из базы.
 *  Здесь, а не в `types.ts`: тот упёрся в предел длины файла. */
export interface StopAdded extends StopEntry {
  /** Домена в базе не было, запись завела его новым: донора с ним нет. */
  new_domain: boolean;
  /** Адреса нет ни у одного донора или рекламодателя, и писем на него не было. */
  new_address: boolean;
  /** Сколько писем запись сняла: из очереди и со сроков добивок. */
  stopped: number;
}

/** Завести запись руками: домен целиком или один адрес. Ссылку, `www.` и поддомен
 *  сервер сводит к домену сайта, как его пишет база, и говорит, знаком ли ей домен
 *  или адрес и сколько писем запись сняла. */
export function addSuppression(body: {
  target: string;
  reason: SuppressionReason;
  /** Пусто — навсегда. Дата в прошлом отвергается сервером. */
  expires_at?: string | null;
}): Promise<StopAdded> {
  return request<StopAdded>('/suppressions', { method: 'POST', body });
}

/** Снять запись. Для решения адресата причина обязательна — её требует
 *  сервер, и она уходит в журнал вместе с именем снявшего. */
export function removeSuppression(id: number, reason: string | null): Promise<StopEntry> {
  return request<StopEntry>(`/suppressions/${id}/remove`, { method: 'POST', body: { reason } });
}
