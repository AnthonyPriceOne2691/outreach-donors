/**
 * «Ждут адреса: N · идёт поиск / · найти» на экране писем рекламодателей — та же строка, что в
 * «К письму» (`usePendingContacts`), а число — ждущих своей вкладки (`LETTERS_CONTACTS`): оно
 * сходится с воронкой и подсказкой у сборки. Тест-группа 29.10.2026: «из 180 бизнесов ниши —
 * 3 письма» — адрес был у восьми, а экран писем молчал, что остальные его ждут и идёт ли поиск.
 * Ждущих нет и сказать нечего — строки нет.
 */

import { Stack } from '@mantine/core';

import { LETTERS_CONTACTS } from '../advertisers/contactsSource';
import type { LetterAudience } from '../api/letters';
import { usePendingContacts } from '../donors/PendingContacts';

export function AddressLine({ audience }: { audience: LetterAudience }) {
  const contacts = usePendingContacts(LETTERS_CONTACTS[audience]);
  if (contacts.control === null && contacts.outcome === null) return null;
  return (
    <Stack gap={4}>
      {contacts.control}
      {contacts.outcome}
    </Stack>
  );
}
