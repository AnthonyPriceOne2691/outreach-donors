/**
 * «Адреса нет» и «Формы» (20.10.2026): поиск адреса глубже, страница формы и текст
 * письма для неё.
 *
 * Поля карточки и строк, которые с этим пришли, описаны здесь, а не в `types.ts`:
 * тот у предела длины, а поля — про одну тему. Все необязательные: сервер старше
 * экрана их не пришлёт, и экран тогда ведёт себя как раньше.
 */

import { request } from './client';
import type { ContactsQueued, DonorFullCard, DonorRowCard, FormCard } from './types';

/** «Искать глубже» у донора: почему нельзя сейчас и чем кончился прошлый раз. */
export interface DeeperCard {
  /** Почему сейчас нельзя — словами сервера; `null` — можно. */
  refusal: string | null;
  /** Когда искали глубже и чем кончилось; `null` — глубже не искали. */
  at: string | null;
  note: string | null;
}

/** Карточка донора с полями «Адреса нет» и «Формы». */
export type DonorCardWithReach = DonorFullCard & {
  /** Почему «адреса нет», если сайт не прочитан: не пустил, проверка «вы не робот». */
  contact_reason?: string | null;
  /** Страница с контактной формой сайта. */
  form_url?: string | null;
  deeper?: DeeperCard;
};

/** Строка доноров: можно ли искать глубже — правилом сервера. */
export type DonorRowWithReach = DonorRowCard & { deeper?: boolean };

/** Строка «Форм»: страница с формой. `null` — искали до 20.10.2026, адрес не записан. */
export type FormRow = FormCard & { form_url?: string | null };

/** Текст письма для формы — тот же, что ушёл бы донору письмом. */
export interface FormLetterCard {
  subject: string;
  body: string;
  /** Чей текст взят: «текст рассылки «…»» или «текст первого письма по умолчанию». */
  based_on: string;
  built_at: string;
  uniqueness_pct: number | null;
  /** Почему часть текста осталась шаблонной — словами сборки. */
  notes: string | null;
  /** Метки незаполненных настроек в тексте: такой текст в форму не вставляют. */
  unset: string[];
}

export interface FormLetterView {
  donor_id: number;
  host: string;
  form_url: string | null;
  letter: FormLetterCard | null;
  /** Задача, которая собирает текст, — поставлена сейчас или ещё идёт. */
  job_id: string | null;
}

/** Искать адрес глубже одному донору. Не ставится — сервер говорит почему. */
export function searchDonorDeeper(donorId: number): Promise<ContactsQueued> {
  return request<ContactsQueued>(`/contacts/donors/${donorId}/deeper`, { method: 'POST' });
}

/** Где форма донора и собранный текст — без вызова модели. */
export function fetchFormLetter(donorId: number): Promise<FormLetterView> {
  return request<FormLetterView>(`/contacts/forms/${donorId}/letter`);
}

/** Собрать текст задачей; собранный сервер отдаёт сразу. `again` — собрать заново. */
export function buildFormLetter(donorId: number, again = false): Promise<FormLetterView> {
  return request<FormLetterView>(`/contacts/forms/${donorId}/letter`, {
    method: 'POST',
    body: { again },
  });
}
