/**
 * Контакты: поиск по кнопке и ручная очередь форм.
 *
 * Поиск идёт задачей, поэтому здесь два запроса, а не один: поставить
 * и спросить, чем кончилось. Держать соединение минутами нельзя —
 * человек закроет вкладку, и работа пропадёт.
 */

import type { ContactsQueued, ContactsState, DonorFullCard, FormCard, FormsView } from './types';
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

/** Вписать донору адрес руками — с его карточки. Ответ — карточка целиком:
 *  адрес меняет исход поиска и то, на какой адрес уйдёт письмо. */
export function addDonorAddress(donorId: number, email: string): Promise<DonorFullCard> {
  return request<DonorFullCard>(`/contacts/donors/${donorId}/addresses`, {
    method: 'POST',
    body: { email },
  });
}

/** Удалить адрес донора, которому ещё не писали. */
export function removeDonorAddress(donorId: number, contactId: number): Promise<DonorFullCard> {
  return request<DonorFullCard>(`/contacts/donors/${donorId}/addresses/${contactId}`, {
    method: 'DELETE',
  });
}

/** Страница ручной очереди, номер с единицы: размер страницы называет сервер. */
export function fetchForms(page: number): Promise<FormsView> {
  return request<FormsView>(`/contacts/forms?page=${page}`);
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

/** Рекламодатель из ручной очереди (сверка ТЗ 11.10.2026): форма есть, адреса нет.
 *  Вместо DR и трафика — откуда он и балл: метрик у рекламодателя нет. */
export interface AdvertiserFormCard {
  advertiser_id: number;
  domain_id: number;
  host: string;
  source: 'links' | 'niche';
  /** Донор, на котором нашли ссылку; у бизнеса ниши — `null`. */
  donor_host: string | null;
  /** Прогон, в выдаче которого нашёлся бизнес ниши. */
  run_id: number | null;
  points: number;
  attempted_at: string | null;
}

export interface AdvertiserFormsView {
  rows: AdvertiserFormCard[];
  total: number;
  page: number;
  limit: number;
  /** Ориентир темпа — один на обе очереди. */
  monthly_left: number;
  monthly_cap: number;
  /** Ждут рук во вкладке доноров. */
  donors_waiting: number;
  /** Через сколько дней после прошлого поиска закрытому «Не вышло» поищут адрес снова. */
  retry_days: number;
}

export function fetchAdvertiserForms(page: number): Promise<AdvertiserFormsView> {
  return request<AdvertiserFormsView>(`/advertisers/forms?page=${page}`);
}

/** Форму заполнили, рекламодатель дал адрес — дальше ему уходит оффер. */
export function advertiserFormFilled(id: number, email: string): Promise<AdvertiserFormCard> {
  return request<AdvertiserFormCard>(`/advertisers/forms/${id}/filled`, {
    method: 'POST',
    body: { email },
  });
}

/** Заполнить не вышло: висеть в очереди вечно рекламодатель не должен. */
export function advertiserFormGaveUp(
  id: number,
  reason: string | null,
): Promise<AdvertiserFormCard> {
  return request<AdvertiserFormCard>(`/advertisers/forms/${id}/give-up`, {
    method: 'POST',
    body: { reason },
  });
}
