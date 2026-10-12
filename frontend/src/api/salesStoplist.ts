/**
 * Стоп-лист продаж: свои записи — клиенты и партнёры, — пополнение списком или файлом, снятие
 * записи; и строки общего стоп-листа, которые держат продажи (их снимают на «Стоп-листе»).
 * Своим файлом: `salesTypes.ts` стоит у предела длины.
 */

import { request, upload } from './client';
import type { SuppressionReason } from './types';

/** Запись стоп-листа продаж: домен или адрес, причина, кто и когда внёс. */
export interface SalesStopRow {
  id: number;
  host: string | null;
  email: string | null;
  note: string | null;
  created_by: string | null;
  created_at: string;
}

/** Строка общего стоп-листа, которая держит продажи: отписался, пожаловался, общий список.
 *  Снимают её на экране «Стоп-лист», не здесь. */
export interface SalesHeldRow {
  id: number;
  target: string;
  reason: SuppressionReason;
  stage: string | null;
  created_at: string;
}

/** Стоп-лист продаж (`GET /sales/stoplist`): свои записи и общие, что держат продажи. */
export interface SalesStoplistView {
  rows: SalesStopRow[];
  total: number;
  /** Общие строки — новые первыми, не все: сколько всего — `general_total`. Без права `view`
   *  сервер их не присылает: пусто и ноль. */
  general: SalesHeldRow[];
  general_total: number;
  /** Предел причины, знаков. */
  note_max: number;
}

/** Строка списка, которая не легла: номер, почему словами сервера, ячейка как есть. */
export interface SalesStopRemark {
  line: number;
  reason: string;
  cell: string;
}

/** Итог пополнения: сколько легло, сколько уже было, сколько лидов снято, что не легло. */
export interface SalesStoplistAdded {
  added: number;
  known: number;
  closed: number;
  remarks: SalesStopRemark[];
}

/** Снятая запись и сколько лидов вернулось на очистку. */
export interface SalesStoplistRemoved {
  id: number;
  target: string;
  released: number;
}

/** Стоп-лист продаж: свои записи и строки общего стоп-листа, что держат продажи. */
export function readSalesStoplist(): Promise<SalesStoplistView> {
  return request<SalesStoplistView>('/sales/stoplist');
}

/** Что вставляют в стоп-лист: список текстом или файл — одно из двух — и причина. */
export interface StoplistInput {
  text: string;
  file: File | null;
  note: string;
}

/** Пополнить стоп-лист продаж — формой, как загрузку базы. Лиды, которых закрыли новые
 *  записи, сервер снимает тем же правилом, что очистка. */
export function addToSalesStoplist(input: StoplistInput): Promise<SalesStoplistAdded> {
  const form = new FormData();
  if (input.file !== null) form.set('file', input.file, input.file.name);
  else form.set('text', input.text);
  if (input.note.trim() !== '') form.set('note', input.note.trim());
  return upload<SalesStoplistAdded>('/sales/stoplist', form);
}

/** Снять запись: лиды, которых закрывала только она, возвращаются на очистку. */
export function removeFromSalesStoplist(id: number): Promise<SalesStoplistRemoved> {
  return request<SalesStoplistRemoved>(`/sales/stoplist/${id}/remove`, { method: 'POST' });
}
