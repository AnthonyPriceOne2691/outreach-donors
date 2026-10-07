/**
 * «Завести донора вручную» — на панели «Обход доноров» (07.10.2026).
 *
 * Требование Этапа 2: «по кому запускаем — только доноры с известной ценой (из
 * базы или заведённые вручную)». Агентство знает цены многих сайтов само — свой
 * прайс, биржи, прошлые сделки, — и без ручного ввода Этап 2 по ним не начать.
 * Домен, которого нет среди доноров, становится донором — принятым человеком,
 * без прогона и без Ahrefs; уже донор — получает цену. Правило одно с карточкой
 * донора и консолью (`outreach donor-add`): отказ — словами сервера над полями,
 * ничего не записано.
 *
 * **Что вышло — словами, а не молчанием**: заведён или уже был донором, и
 * возьмёт ли его обход. Донор, не прошедший пороги после обновления метрик,
 * цену получает, а в обход не попадает — это сказано, и уведомление янтарное.
 */

import { Stack, Text, TextInput } from '@mantine/core';
import { notifications } from '@mantine/notifications';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';

import type { EnteredDonor } from '../api/manualPrice';
import { enterDonor } from '../api/runs';
import { NEW_PRICE, PriceForm, priceBody, priceTyped } from '../donors/PriceForm';
import type { PriceDraft } from '../donors/PriceForm';
import { formatMoney } from '../format';

/** Что вышло — одной фразой, теми же словами, что у консоли. */
export function enteredSummary(entered: EnteredDonor): string {
  const money = formatMoney(entered.price, entered.currency);
  const what = entered.created
    ? `Донор ${entered.host} заведён вручную: ${money}.`
    : `${entered.host} уже был донором — записана цена ${money}.`;
  return entered.suitable
    ? `${what} Обход Этапа 2 его берёт.`
    : `${what} По порогам отбора он не годен — обход Этапа 2 его не возьмёт.`;
}

export function ManualDonor({
  id,
  onEntered,
  onCancel,
}: {
  id: string;
  /** Донор заведён — таблице панели пора перечитаться. */
  onEntered: () => void;
  onCancel: () => void;
}) {
  const [host, setHost] = useState('');
  const [draft, setDraft] = useState<PriceDraft>(NEW_PRICE);
  const enter = useMutation({
    mutationFn: () => enterDonor({ ...priceBody(draft), host: host.trim() }),
    onSuccess: (entered) => {
      setHost('');
      setDraft(NEW_PRICE);
      notifications.show({
        message: enteredSummary(entered),
        color: entered.suitable ? 'green' : 'yellow',
      });
      onEntered();
    },
  });
  return (
    <Stack gap="sm">
      <Text size="sm" c="dimmed" maw={720}>
        Цена, которую агентство знает само: свой прайс, биржа, прошлая сделка. Домен, которого нет
        среди доноров, станет донором без прогона и без Ahrefs — метрик у него не будет, пока его не
        найдёт прогон.
      </Text>
      <PriceForm
        id={id}
        lead={
          <TextInput
            label="Домен"
            placeholder="example.com"
            value={host}
            style={{ flex: '1 1 14rem', maxWidth: '20rem' }}
            onChange={(event) => {
              setHost(event.currentTarget.value);
              enter.reset();
            }}
          />
        }
        draft={draft}
        onChange={(next) => {
          setDraft(next);
          enter.reset();
        }}
        ready={host.trim() !== '' && priceTyped(draft)}
        pending={enter.isPending}
        error={enter.error}
        refused="Донора не завели"
        submit="Завести донора"
        onSubmit={() => enter.mutate()}
        onCancel={onCancel}
      />
    </Stack>
  );
}
