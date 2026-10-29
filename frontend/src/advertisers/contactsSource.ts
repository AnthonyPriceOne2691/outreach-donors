/**
 * Поиск адресов рекламодателям — запросы для общей строки «ждут адреса · найти»
 * (`donors/PendingContacts.usePendingContacts`). Один источник на «К письму» и экран писем
 * рекламодателей (29.10.2026): две копии разъехались бы на первой правке.
 */

import { fetchAdvertiserContacts, searchAdvertiserContacts } from '../api/advertisers';
import type { LetterAudience } from '../api/letters';
import type { ContactsSource } from '../donors/PendingContacts';

export const PROMOTION_QUERY_KEY = ['advertisers-promotion'] as const;

/** Кончился поиск — перечитать «К письму» и очередь писем: у кого-то появились адреса,
 *  и воронка «Собрать очередь» уже другая. */
export const ADVERTISER_CONTACTS: ContactsSource = {
  queryKey: ['advertiser-contacts'],
  // Обёрткой: запрос зовёт `fetchState` со своим контекстом, он ушёл бы аудиторией.
  fetchState: () => fetchAdvertiserContacts(),
  search: searchAdvertiserContacts,
  refresh: [PROMOTION_QUERY_KEY, ['letters']],
};

/** Вкладки писем рекламодателей: «ждут адреса» — числом своей вкладки, как её воронка; «найти»
 *  и «идёт поиск» — общие: задача поиска одна на всех. Кончился поиск — перечитать и «К письму». */
function lettersContacts(audience: LetterAudience): ContactsSource {
  return {
    queryKey: [...ADVERTISER_CONTACTS.queryKey, audience],
    fetchState: () => fetchAdvertiserContacts(audience),
    search: searchAdvertiserContacts,
    refresh: [...ADVERTISER_CONTACTS.refresh, ADVERTISER_CONTACTS.queryKey],
  };
}

export const LETTERS_CONTACTS: Record<LetterAudience, ContactsSource> = {
  links: lettersContacts('links'),
  niche: lettersContacts('niche'),
};
