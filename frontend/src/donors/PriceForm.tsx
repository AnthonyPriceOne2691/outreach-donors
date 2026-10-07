/**
 * Форма цены, которую человек знает сам: цена, валюта, откуда цена — и отказ
 * сервера над полями.
 *
 * Одна у «Указать цену» в карточке донора и у «Завести донора вручную» на
 * панели «Обход доноров» (у панели перед ценой — домен): правило записи одно
 * (`donors/manual_price.py`), и две копии формы разошлись бы на первой правке
 * одной из них.
 *
 * **Проверяет сервер, словами.** Цена уходит строкой, как вписана: «1,200»
 * бывает и тысячей двумястами, и единицей с копейками, — сервер не угадывает,
 * а говорит, как написать. Потолка цены, списка валют и длины заметки у экрана
 * нет: своя копия правил разошлась бы с сервером, а его отказ и так приходит
 * словами — над полями, и введённое остаётся на месте.
 */

import { Alert, Button, Group, Stack, TextInput } from '@mantine/core';
import type { ReactNode } from 'react';

import { refusalOf } from '../api/client';
import type { PriceBody } from '../api/manualPrice';

export interface PriceDraft {
  price: string;
  currency: string;
  note: string;
}

/** Пустая форма. Валюта вписана сразу: так названо большинство цен рынка,
 *  и человек видит, в чём запишется число, а не узнаёт после. */
export const NEW_PRICE: PriceDraft = { price: '', currency: 'USD', note: '' };

/** Тело запроса из вписанного. Пустая заметка — «не сказали», а не пустая строка. */
export function priceBody(draft: PriceDraft): PriceBody {
  const note = draft.note.trim();
  return {
    price: draft.price.trim(),
    currency: draft.currency.trim(),
    note: note === '' ? null : note,
  };
}

/** Есть что отправлять: без цены сервер только скажет, что её нет. */
export function priceTyped(draft: PriceDraft): boolean {
  return draft.price.trim() !== '';
}

interface Props {
  id: string;
  draft: PriceDraft;
  onChange: (next: PriceDraft) => void;
  /** Поля перед ценой: домен у «Завести донора вручную». */
  lead?: ReactNode;
  /** Всё нужное вписано — кнопка нажимается. */
  ready: boolean;
  pending: boolean;
  error: Error | null;
  /** Заголовок отказа: «Цену не записали», «Донора не завели». */
  refused: string;
  submit: string;
  onSubmit: () => void;
  onCancel: () => void;
}

export function PriceForm({ id, draft, onChange, lead, ready, pending, error, ...form }: Props) {
  const set = (field: keyof PriceDraft) => (event: { currentTarget: HTMLInputElement }) =>
    onChange({ ...draft, [field]: event.currentTarget.value });
  return (
    <form
      id={id}
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) form.onSubmit();
      }}
    >
      <Stack gap="sm">
        {error ? (
          <Alert color="red" title={form.refused}>
            {refusalOf(error)}
          </Alert>
        ) : null}
        <Group gap="sm" align="flex-start" wrap="wrap">
          {lead}
          <TextInput
            label="Цена"
            placeholder="150"
            inputMode="decimal"
            value={draft.price}
            w={140}
            onChange={set('price')}
          />
          <TextInput
            label="Валюта"
            placeholder="USD, €"
            value={draft.currency}
            w={110}
            onChange={set('currency')}
          />
          <TextInput
            label="Откуда цена"
            placeholder="прайс агентства, LinkDetective"
            value={draft.note}
            style={{ flex: '1 1 14rem', maxWidth: '22rem' }}
            onChange={set('note')}
          />
        </Group>
        {/* Кнопка — действие, а не четвёртое поле: отдельным рядом. */}
        <Group gap="xs">
          <Button type="submit" className="press" loading={pending} disabled={!ready}>
            {form.submit}
          </Button>
          <Button variant="default" className="press" onClick={form.onCancel}>
            Отмена
          </Button>
        </Group>
      </Stack>
    </form>
  );
}
