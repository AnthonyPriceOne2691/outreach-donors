/**
 * Цены списком: всё, что донор назвал в ответе, — под ценой в карточке
 * ответа и рядом с последней ценой в карточке донора.
 *
 * Главная цена одна — гостевого поста, а донор называет и вставку ссылки,
 * и ссылку на главной, и свою цену для казино. До 06.10.2026 они уходили
 * в заметку модели, которая не хранится, и человек решал, не видя их.
 *
 * **Продукт и ниша — словами донора** («guest post · casino»): это цитата
 * письма, и перевод был бы пересказом. Свои слова — по-русски: «в месяц»,
 * «в год». **Деньги — общей функцией** (`formatMoney`), как цена рядом:
 * одна сумма двумя записями на одной карточке читается как два числа.
 *
 * Пустой список и список, которого нет (ответ разобран до 06.10.2026), не
 * рисуются вовсе: о цене уже сказано рядом.
 */

import { Stack, Text } from '@mantine/core';

import type { Offer } from '../api/offers';
import { formatMoney } from '../format';

const TITLE = 'Все цены из ответа';

/** Срок цены нашими словами. */
const PERIODS: Record<NonNullable<Offer['period']>, string> = {
  month: 'в месяц',
  year: 'в год',
};

/** Строка списка: «guest post · casino — 300,00 $ в месяц». */
export function offerLine(offer: Offer): string {
  const what = offer.niche ? `${offer.product} · ${offer.niche}` : offer.product;
  const period = offer.period ? ` ${PERIODS[offer.period]}` : '';
  return `${what} — ${formatMoney(offer.price, offer.currency)}${period}`;
}

/** `offers` необязательно: у ответов, записанных до списка, поля нет вовсе. */
export function ReplyOffers({ offers }: { offers?: Offer[] | null | undefined }) {
  if (!offers?.length) return null;
  return (
    <>
      <Text size="xs" c="dimmed" mt="sm" mb={4}>
        {TITLE}
      </Text>
      <Stack gap={2} component="ul" className="replyOffers" aria-label={TITLE}>
        {offers.map((offer, index) => (
          <Text key={`${index}-${offer.product}`} size="sm" component="li">
            {offerLine(offer)}
          </Text>
        ))}
      </Stack>
    </>
  );
}
