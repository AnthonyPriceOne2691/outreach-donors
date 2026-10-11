/**
 * Правка лида с экрана «Лиды»: имя, должность, компания, язык, страна и пояс. Своим файлом:
 * `salesTypes.ts` стоит у предела длины.
 */

import { request } from './client';
import type { LeadCard } from './salesTypes';

/** Правка лида с экрана (`PATCH /sales/leads/{id}`): только присланные поля; пусто или
 *  `null` — «не задано». Правила полей — сервера, как у загрузки базы. */
export interface LeadPatchBody {
  name?: string | null;
  position?: string | null;
  company?: string | null;
  language?: string | null;
  country?: string | null;
  timezone?: string | null;
}

/** Лид после правки — строка той же формы, что в списке, — и что изменилось. */
export interface LeadChanged extends LeadCard {
  /** Поля, которые правка изменила; пусто — всё было так же. */
  changed: string[];
  /** Первое письмо лида ждёт в очереди — следующая сборка соберёт его заново. */
  rebuild: boolean;
}

/** Поправить лида: имя, должность, компания, язык, страна и пояс. Ответ — строка лида и что
 *  изменилось; письмо в очереди после правки соберёт заново следующая сборка (`rebuild`). */
export function changeLead(id: number, body: LeadPatchBody): Promise<LeadChanged> {
  return request<LeadChanged>(`/sales/leads/${id}`, { method: 'PATCH', body });
}
