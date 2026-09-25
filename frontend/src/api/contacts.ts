/**
 * Контакты: поиск по кнопке и ручная очередь форм.
 *
 * Поиск идёт задачей, поэтому здесь два запроса, а не один: поставить
 * и спросить, чем кончилось. Держать соединение минутами нельзя —
 * человек закроет вкладку, и работа пропадёт.
 */

import type { ContactsQueued, ContactsState, FormCard, FormsView } from './types';
import { request } from './client';

export function fetchContactsState(): Promise<ContactsState> {
  return request<ContactsState>('/contacts');
}

export function searchContacts(body: {
  limit?: number;
  use_browser?: boolean;
}): Promise<ContactsQueued> {
  return request<ContactsQueued>('/contacts', { method: 'POST', body });
}

/** Поиск адреса одному донору — с его карточки. Та же задача, что у общего
 *  поиска, суженная до донора; не ставится — сервер говорит почему. */
export function searchDonorContact(donorId: number): Promise<ContactsQueued> {
  return request<ContactsQueued>(`/contacts/donors/${donorId}`, { method: 'POST' });
}

export function fetchForms(): Promise<FormsView> {
  return request<FormsView>('/contacts/forms');
}

/** Форму заполнили, донор дал адрес — дальше он обычный донор. */
export function formFilled(donorId: number, email: string): Promise<FormCard> {
  return request<FormCard>(`/contacts/forms/${donorId}/filled`, {
    method: 'POST',
    body: { email },
  });
}

/** Заполнить не вышло: висеть в очереди вечно донор не должен. */
export function formGaveUp(donorId: number, reason: string | null): Promise<FormCard> {
  return request<FormCard>(`/contacts/forms/${donorId}/give-up`, {
    method: 'POST',
    body: { reason },
  });
}
