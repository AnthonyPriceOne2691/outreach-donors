/**
 * Чем кончился поиск адреса — строкой под заголовком «Адреса» карточки донора: когда
 * искали и, если сайт не прочитан, почему; у «только формы» — где форма и что дальше.
 *
 * Вынесено из `DonorAddresses.tsx`, когда сюда пришли причина и ссылка на форму
 * (20.10.2026): «адреса нет» у сайта, который нас не пустил, читалось как «адреса на
 * сайте нет», а ссылка «открыть сайт» оставляла форму искать по нему руками.
 */

import { Anchor, Text } from '@mantine/core';
import { Link } from 'react-router-dom';

import type { DonorCardWithReach } from '../api/deeper';
import { formatDate } from '../format';

export function SearchState({ donor }: { donor: DonorCardWithReach }) {
  if (donor.contact_attempted_at !== null) {
    // Причина — только у «адреса нет» и «не спросили» (сайт не прочитан): у «сайт не
    // ответил» её говорит отказ поиска ниже, и две похожие строки читались бы как сбой.
    const unread = donor.contact_status === 'not_found' || donor.contact_status === 'paid_off';
    const why = unread ? donor.contact_reason : null;
    return (
      <Text size="sm" c="dimmed">
        Искали {formatDate(donor.contact_attempted_at)}
        {why ? `: ${why}` : ''}.
      </Text>
    );
  }
  // Вписанный руками адрес — «адрес найден», но поиска не было: дата
  // поиска здесь была бы выдумкой.
  const written = donor.contacts.some((contact) => contact.source === 'manual');
  return (
    <Text size="sm" c="dimmed">
      {written ? 'Адрес вписан вручную, лестницей не искали.' : 'Адрес ещё не искали.'}
    </Text>
  );
}

/** Почему донора нет в очереди «Формы» — тем, что она проверяет (`forms.in_queue`):
 *  кандидата она не берёт, принятого — если ему не пишем. */
function notQueued(accepted: boolean): string {
  if (!accepted) return 'В очередь «Формы» встают только доноры, принятые человеком.';
  return (
    'В очередь «Формы» он не встаёт: форма — тоже письмо, а сайту в стоп-листе, ' +
    'в «кому не пишем» или в работе у другого направления не пишем.'
  );
}

/** Только форма: где её заполнить и что будет дальше. В очереди «Формы» — только
 *  доноры (`contacts/forms.py`, `in_queue`): у кандидата карточка писала «Донор стоит
 *  в очереди», а очередь его не брала (проверка прода 10.10.2026). Не берёт она и донора,
 *  которому не пишем, — стоп-лист, «кому не пишем», сайт в работе у другого направления
 *  (28.10.2026), — поэтому стоит ли, говорит сервер её условием (`in_forms`); сервер старше
 *  экрана — по решению человека, как раньше. Где форма, лестница помнит с 20.10.2026;
 *  найденная раньше — ссылкой на сайт. */
export function FormOnly({ donor }: { donor: DonorCardWithReach }) {
  const accepted = donor.review === 'accepted';
  const queued = donor.in_forms ?? accepted;
  return (
    <Text size="sm">
      Адреса нет — на сайте только форма, её заполняет человек:{' '}
      <Anchor
        href={donor.form_url ?? `https://${donor.host}`}
        target="_blank"
        rel="noreferrer"
        size="sm"
      >
        {donor.form_url ? 'открыть форму' : 'открыть сайт'}
      </Anchor>
      .{' '}
      {queued ? (
        <>
          Донор стоит в очереди{' '}
          <Anchor component={Link} to="/forms" size="sm">
            «Формы»
          </Anchor>
          : там же текст письма для формы; ответ на форму письмом даст адрес, и ему уйдёт письмо из
          очереди.
        </>
      ) : (
        notQueued(accepted)
      )}
    </Text>
  );
}
